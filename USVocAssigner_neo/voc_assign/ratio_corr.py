# [vocassign/ratio_corr.py] 위치·방향 기반 후보 마우스 scoring
# ★ 검증 전 가정: 자유음장 압력 감쇠(1/r), 선택적인 경험적 방향성 모델.
# observed channel dB = unknown source level + predicted attenuation + noise.
# source level은 모든 채널에서 공통이므로 '평균을 뺀 채널별 패턴'을 비교한다.
# 영상의 pixel 좌표를 m로 바꾸는 HOMOGRAPHY와 마이크 실제 위치가 필수다.
# 2D 근사는 마이크-마우스 높이 차이/반사/가림을 모델링하지 않는다.
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class GeometryConfig:
    mic_positions_m: dict           # {"ch1": [x_m, y_m], ...}
    pixel_to_meter_h: np.ndarray     # 3x3 카메라 평면 Homography
    recording_bias_db: dict | None = None
    back_loss_db: float = 0.0        # 방향성 세기. 0이면 비활성.
    max_error_db: float = 12.0       # 후보 채널 패턴의 허용 RMSE (검증 전)
    min_gap_db: float = 2.0          # 최고-차점 RMSE 차이 기준 (검증 전)
    min_distance_m: float = 0.01     # log(0) 예방

    def __post_init__(self):
        H = np.asarray(self.pixel_to_meter_h, dtype=float)
        if H.shape != (3, 3) or not np.isfinite(H).all():
            raise ValueError("pixel_to_meter_h must be a finite 3x3 matrix.")
        if self.mic_positions_m and len(self.mic_positions_m) < 2:
            raise ValueError("Geometry requires at least two microphone positions.")
        if any(np.asarray(v).shape != (2,) for v in self.mic_positions_m.values()):
            raise ValueError("Microphone positions must be 2D metric coordinates.")
        if self.back_loss_db < 0 or self.min_gap_db < 0 or self.max_error_db <= 0:
            raise ValueError("Invalid geometry thresholds.")


def pixel_to_m(point_xy_px, matrix_h):
    """영상 픽셀 평면 좌표를 보정된 실측 좌표(m)로 투영."""
    px = np.array([*point_xy_px, 1.0], dtype=float)
    projected = np.asarray(matrix_h, dtype=float) @ px
    if not np.isfinite(projected).all() or abs(projected[2]) < 1e-12:
        raise ValueError("Pixel-to-meter transformation failed.")
    return projected[:2] / projected[2]


def geometric_features(mouse_xy_m, mic_xy_m, heading_xy):
    """후보 마우스→마이크 거리(m), 마우스 전방과 마이크 방향의 각(rad)."""
    r = np.asarray(mic_xy_m, float) - np.asarray(mouse_xy_m, float)
    h = np.asarray(heading_xy, float)
    d = np.linalg.norm(r)
    nh = np.linalg.norm(h)
    if d <= 0 or nh <= 0 or not np.isfinite(d + nh):
        return float("nan"), float("nan")
    cos_angle = np.clip(np.dot(h, r) / (d * nh), -1.0, 1.0)
    return float(d), float(np.arccos(cos_angle))


def expected_attenuation_db(mouse_xy_px, heading_xy_px, config: GeometryConfig,
                            channel_ids: list[str]):
    """채널별 예측 감쇠[dB]; 배열 순서는 전달된 channel_ids와 동일."""
    center_m = pixel_to_m(mouse_xy_px, config.pixel_to_meter_h)
    # 방향 벡터는 homography 비선형성을 고려해 두 점을 각각 변환해서 계산.
    nose_px = np.asarray(mouse_xy_px, float)
    torso_px = nose_px - np.asarray(heading_xy_px, float)
    heading_m = center_m - pixel_to_m(torso_px, config.pixel_to_meter_h)
    atten = []
    for cid in channel_ids:
        if cid not in config.mic_positions_m:
            raise KeyError(f"Geometry missing microphone position for {cid}")
        d, angle = geometric_features(center_m, config.mic_positions_m[cid], heading_m)
        if not np.isfinite(d + angle):
            raise ValueError("Invalid mouse position/heading.")
        # 음압 거리 모델의 절대 기준항은 채널 평균 제거에서 상쇄된다.
        distance_db = -20.0 * np.log10(max(d, config.min_distance_m))
        # front=0dB, back=-back_loss_db: 임의 경험식이므로 0부터 실험으로 검증.
        angle_db = -config.back_loss_db * (1.0 - np.cos(angle)) / 2.0
        atten.append(distance_db + angle_db)
    return np.asarray(atten, dtype=float)


def compare_candidate(levels_db: dict, mouse_xy_px, heading_xy_px,
                      config: GeometryConfig):
    """관측 채널 간 log-level 차와 후보의 예측 패턴 사이 RMSE를 반환."""
    ids = list(config.mic_positions_m)
    if len(ids) < 2:
        raise ValueError("Microphone array pattern model requires two or more mic locations.")
    if any(cid not in levels_db or not np.isfinite(levels_db[cid]) for cid in ids):
        return float("nan")
    # run_assignment에서 이미 recording_bias를 제거했으므로 중복 보정하지 않는다.
    observed = np.array([levels_db[cid] for cid in ids], dtype=float)
    predicted = expected_attenuation_db(mouse_xy_px, heading_xy_px, config, ids)
    # 알 수 없는 source level을 제거: 중심화로 모든 후보를 공정하게 비교한다.
    residual = (observed - np.mean(observed)) - (predicted - np.mean(predicted))
    return float(np.sqrt(np.mean(residual ** 2)))


# ==============================================================
# Empirical geometry reliability weighting — 사용자 수식의 구현.
# 적용 대상: 두 개체(A/B)가 추적되고 각 개체의 대응 음향 채널이 확인된 실험.
#
# <문서 수식의 차이> 앞부분 omega_geo=omega_d*omega_theta,
# 후반부 omega_geo=1-omega_d*omega_theta. 두 가지를 선택 가능하게 구현한다.
# 기본 product 모드는 reliability 해석에 부합하며 complement는 비교실험용이다.
# 같은 validation split에서 두 식의 결과/coverage를 반드시 비교한다.
# 물리 감쇠의 역함수가 아니라 empirical weighting model이며 validation 필수.
# ==============================================================
from dataclasses import dataclass as _dataclass


@_dataclass(frozen=True)
class EmpiricalGeoConfig:
    """검증용 기하학 파라미터. 길이 단위는 모두 미터(m)로 통일."""
    body_length_m: float
    body_width_m: float
    beta: float = 1.0
    alpha_theta: float = 1.0
    omega_min: float = 0.3
    k: float = 1.0
    # 문서에 등장하는 서로 다른 정의를 모두 지원하되 학습/평가 시 동일 모드 사용.
    # product: omega_geo = omega_d * omega_theta (기본, reliability 해석)
    # complement: omega_geo = 1 - omega_d * omega_theta (후반 수식 재현)
    geometry_weight_mode: str = "product"

    def __post_init__(self):
        values = (self.body_length_m, self.body_width_m,
                  self.beta, self.omega_min, self.k)
        if any(not np.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Positive finite L, W, beta, omega_min and k required.")
        if not 0 < self.omega_min <= 1:
            raise ValueError("omega_min must satisfy 0 < omega_min <= 1.")
        if not np.isfinite(self.alpha_theta) or self.alpha_theta < 0:
            raise ValueError("alpha_theta must be nonnegative.")
        if self.geometry_weight_mode not in ("product", "complement"):
            raise ValueError("geometry_weight_mode must be 'product' or 'complement'.")


@_dataclass(frozen=True)
class EmpiricalThreshold:
    """학습/검증 데이터에서 얻은 경계. 여기의 MAD는 unscaled raw MAD."""
    r_mid: float
    mad: float
    k: float = 1.0

    def __post_init__(self):
        if not np.isfinite(self.r_mid) or not np.isfinite(self.mad):
            raise ValueError("Finite median and MAD required.")
        if self.mad < 0 or self.k <= 0:
            raise ValueError("MAD >= 0 and k > 0 required.")


def fit_empirical_threshold(training_r_corr, k=1.0):
    """미리 정의한 TRAINING 분할에서만 경계 학습; 평가 세트 사용 금지.

    실제 A/B 정답 라벨이 없는 median/MAD만으로는 정확도를 증명할 수 없다.
    클래스 불균형과 0 MAD에 주의. 임계값은 별도 validation으로 튜닝한다.
    """
    arr = np.asarray(training_r_corr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        raise ValueError("At least 2 finite corrected ratios required.")
    mid = float(np.median(arr))
    mad = float(np.median(np.abs(arr - mid)))
    if mad == 0:
        raise ValueError("MAD=0; validation labels/more diverse training data required.")
    return EmpiricalThreshold(mid, mad, float(k))


def ellipse_ray_length(theta, config: EmpiricalGeoConfig):
    """코/입을 장축 끝(a,0)으로 근사한 쥐 타원 내부의 반직선 길이.

    l_i(theta) = max(0, 2*a*b^2*cos(theta) /
       (b^2*cos(theta)^2 + a^2*sin(theta)^2)).
    등호는 뒤를 향하는 선의 타원 내부 통과 길이가 0인 근사를 뜻한다.
    """
    a, b = config.body_length_m / 2, config.body_width_m / 2
    ct, st = np.cos(theta), np.sin(theta)
    denom = b*b*ct*ct + a*a*st*st
    return float(max(0., (2*a*b*b*ct)/denom))


def empirical_geometry_weights(p_a, p_b, u_a, u_b,
                               config: EmpiricalGeoConfig):
    """두 nose 좌표(m), 전방 단위방향 벡터로 거리/타원/최종 가중치 계산.

    동일 좌표계로 변환된 좌표만 전달해야 한다.
    d=0이면 마우스가 겹친 상태이므로 기하학적으로 신뢰 불가 → reject.
    """
    p_a, p_b = np.asarray(p_a, float), np.asarray(p_b, float)
    u_a, u_b = np.asarray(u_a, float), np.asarray(u_b, float)
    if any(x.shape != (2,) or not np.isfinite(x).all()
           for x in (p_a, p_b, u_a, u_b)):
        raise ValueError("All positions/directions must be finite 2-vectors.")
    d = float(np.linalg.norm(p_b - p_a))
    na, nb = np.linalg.norm(u_a), np.linalg.norm(u_b)
    if d <= 1e-12 or na <= 1e-12 or nb <= 1e-12:
        raise ValueError("Coincident noses or undefined mouse direction.")
    r_ab = (p_b - p_a)/d
    r_ba = -r_ab
    theta_a = float(np.arccos(np.clip(np.dot(u_a/na, r_ab), -1., 1.)))
    theta_b = float(np.arccos(np.clip(np.dot(u_b/nb, r_ba), -1., 1.)))
    length = ellipse_ray_length(theta_a, config) + ellipse_ray_length(theta_b, config)
    omega_d = float(-np.expm1(-d/(config.beta*config.body_width_m)))
    omega_theta = float(np.exp(-config.alpha_theta*length/config.body_length_m))
    product_weight = omega_d * omega_theta
    # 원본 프롬프트의 수식이 앞/뒤에서 서로 달라 두 모델을 선택 가능하게 함.
    # 어느 정의가 더 정확한지는 validation dataset으로 확인해야 한다.
    omega_geo = (product_weight if config.geometry_weight_mode == "product"
                 else 1.0 - product_weight)
    omega_safe = max(omega_geo, config.omega_min)
    return {"distance_m": d, "theta_a_rad": theta_a, "theta_b_rad": theta_b,
            "l_m": length, "omega_d": omega_d, "omega_theta": omega_theta,
            "omega_geo": omega_geo, "omega_safe": omega_safe}


def empirical_correct_ratio(level_a_db, level_b_db, p_a_m, p_b_m,
                            u_a_m, u_b_m, config: EmpiricalGeoConfig,
                            distance_only=False):
    """STFT dB magnitude 레벨을 원래 문서의 자연로그 진폭비로 변환한다.

    R_amp = ln(A_amp/B_amp) = ln(10)/20 * (L_A_dB - L_B_dB).
    두 채널의 감도/증폭도 보정을 마친 값이어야 한다.
    """
    weights = empirical_geometry_weights(p_a_m, p_b_m, u_a_m, u_b_m, config)
    amp_log_ratio = (float(level_a_db) - float(level_b_db))*np.log(10)/20
    if not np.isfinite(amp_log_ratio):
        raise ValueError("Channel amplitude levels must be finite.")
    reliability = (max(weights["omega_d"], config.omega_min)
                   if distance_only else weights["omega_safe"])
    return float(amp_log_ratio / reliability), weights


def empirical_decision(r_corr, threshold: EmpiricalThreshold, id_a, id_b):
    """중앙값±k×MAD 기준 A/B 또는 unassigned 반환(경계 자체는 미할당)."""
    high = threshold.r_mid + threshold.k*threshold.mad
    low = threshold.r_mid - threshold.k*threshold.mad
    if not np.isfinite(r_corr):
        return None
    if r_corr > high:
        return id_a
    if r_corr < low:
        return id_b
    return None


def validate_empirical_predictions(predicted, ground_truth):
    """미할당률을 숨기지 않고 coverage·조건부 정확도·전체 정확도를 분리."""
    prediction, truth = list(predicted), list(ground_truth)
    if len(prediction) != len(truth) or not truth:
        raise ValueError("Equal nonempty predicted/ground truth lists required.")
    kept = [(p, y) for p, y in zip(prediction, truth) if p is not None]
    correct = sum(p == y for p, y in kept)
    return {"n": len(truth), "assigned": len(kept),
            "coverage": len(kept)/len(truth),
            "accuracy_assigned": correct/len(kept) if kept else float("nan"),
            "accuracy_all": correct/len(truth)}

