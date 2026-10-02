#voltage -> masked voltage
#get the amplitude from voltage and correct the amplitude data


import librosa as lb
import matplotlib
import numpy as np
import scipy as sc

global p_ref
p_ref = 2e-5


#setting the values for STFT
def set_stft() : 
    print("="*20,"\n\n"
        "{Short Term Fourier Transform(STFT) Settings}\n"
        )
    n_fft = int(input("n_fft(FFT window size)\n >> "))
    hop_length = int(input("hop_length(the step size between analysis windows)\n >> "))
    print("\n","="*20, "\n")
        
    return n_fft, hop_length

def mask_label(Data_aud) : 
    voltage_mic = Data_aud["voltage"]
    time = Data_aud["time"]
    duration_idx = Data_aud["labels"]
    sr = Data_aud["sr"]
    #make the mask for the label
    mask = np.zeros_like(time, dtype=bool)

    time_idx = []

    for i in duration_idx : 
        onset_idx = i[0]
        offset_idx = i[1]
        mask[onset_idx:offset_idx] = True

    masked_voltage = np.where(mask, voltage_mic, 0)

    return masked_voltage

def extract_usv(sr, onsets, offsets) :

    labels_idx = []

    for i in range(len(onsets)) : 
        onset_idx = int(onsets[i] * sr)
        offset_idx = int(offsets[i] * sr)
        labels_idx.append([onset_idx, offset_idx])

    return labels_idx

def get_amplitude(IDs, Data_aud) : 

    for ID in IDs : 
        if "response_freqs" in Data_aud[ID] : 
            Data_aud[ID] = cal_abs_db(Data_aud[ID])
        else : 
            Data_aud[ID] = cal_sens_db(Data_aud[ID])

    return Data_aud




#===================================================#

#voltage preprocessing, removing the gain 
def prepre_process(voltage, gain) : 
    #remove DC bias
    voltage_rem_bias = voltage - np.mean(voltage)
    #remove amplifier gain
    voltage_mic = voltage_rem_bias / gain
    """If the circuit has variable frequency response,
    you have to add some code here to correct the values"""
    return voltage_mic

#===================================================#

def do_stft(IDs, Data_aud, n_fft, hop_length) : 

    for ID in IDs : 
        voltage = Data_aud[ID]["masked_voltage"]
        sr = Data_aud[ID]["sr"]

        stft_result = lb.stft(
        voltage, 
        n_fft = n_fft, 
        hop_length = hop_length,
        window = 'hann',
        center = False
        )

        #set the window
        window_type = 'hann'
        window = sc.signal.windows.get_window(
            window_type,
            n_fft,
            fftbins=True
        )
        #define window_sum to use for 
        #recovering the amplitude of sin wave 
        window_sum = np.sum(window)

        magnitude = np.abs(stft_result)
        voltage_peak = magnitude / window_sum

        if n_fft % 2 == 0 : 
            voltage_peak[1:-1, :] *= 2
        else : 
            voltage_peak[1:, :] *= 2

        voltage_rms = voltage_peak / np.sqrt(2)
        frequency = np.fft.rfftfreq(n_fft, d=1/sr )

        Data_aud[ID]["stft_result"] = stft_result
        Data_aud[ID]["voltage_rms"] = voltage_rms
        Data_aud[ID]["frequency"] = frequency
    

    return Data_aud

#===================================================#


#gain frequency_correction_db
#calculate dB SPL
def cal_abs_db(Data_aud) :
    voltage_rms = Data_aud["voltage_rms"]
    sensitivity_dbv_pa = Data_aud["sensitivity"]
    stft_result = Data_aud["stft_result"]
    frequency = Data_aud["frequency"]
    response_freqs = Data_aud["response_freqs"]
    response_db = Data_aud["response_db"]

    #correct frequency by using frequency response
    frequency_correction_db = np.interp( 
        frequency, 
        response_freqs, 
        response_db
        )

    #correct frequency by using sensitivity
    sensitivity_freq_corr_db = (
        sensitivity_dbv_pa 
        + frequency_correction_db
        )

    #dBV/pa->V/pa
    sensitivity_freq_corr_v = (
        10**(sensitivity_freq_corr_db/20)
        )

    #v_min->v_rms
    pressure_rms = voltage_rms / sensitivity_freq_corr_v

    spl_db = 20 * np.log10(
        np.maximum(
            pressure_rms,
            np.finfo(float).tiny
        ) / p_ref )

    Data_aud["amplitude"] = spl_db

    return Data_aud


def cal_sens_db(Data_aud) : 

    sensitivity_dbv_pa = Data_aud["sensitivity"]
    voltage_rms = Data_aud["voltage_rms"]
    voltage = Data_aud["masked_voltage"]

    sensitivity_v_pa = 10 ** (sensitivity_dbv_pa / 20)

    #v_min->v_rms
    pressure_rms = voltage_rms / sensitivity_v_pa

    spl_db = 20 * np.log10(
        np.maximum(
            pressure_rms,
            np.finfo(float).tiny
        ) / p_ref)

    Data_aud["amplitude"] = spl_db

    return Data_aud
