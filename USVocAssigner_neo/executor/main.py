# [executor/main.py] Qt GUI 엔트리포인트 + 다크 테마 설정
# style.qss는 Qt Designer의 .ui 파일과 별개로 관리하는 시각 스타일 파일이다.
# Fusion 스타일을 사용하면 Windows 버전에 따른 기본 위젯 색상 차이를 줄인다.
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from ui.controller import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # 표준 Qt widget painter 사용
    qss_path = Path(__file__).resolve().parents[1] / "ui" / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    else:
        print(f"WARNING: Dark theme file not found: {qss_path}")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())