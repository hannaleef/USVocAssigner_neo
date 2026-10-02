# import files
# the file to analyze : Wav, freq_resp of mic,
# gain function(if it exists), Label file(to increase the accuracy)

#custom modules
import audio.aud_process as ap
import audio.voc_label as vl

#file path reading
import tkinter as tk
from tkinter import filedialog
import sys
import csv
from matplotlib.ticker import SymmetricalLogLocator
import scipy as sc
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")



def get_info(num_mic) : 

    IDs =list(range(num_mic))
    Data_path = {}
    Data_aud = {}
    Data_label = {}

    
    for i in range(num_mic) : 

	    #select and read the file
        #Get the ID information and audio file path
        IDs[i] = (input("Enter your microphone's or subject's ID. \n"
                          f"Channel {i+1} >> "))
        print("\n","="*28,"\n   YOUR FILE PATH  \n","   >> ",end="")
        root = tk.Tk()
        root.withdraw()

        aud_path = filedialog.askopenfilename(
        title="audio 파일 선택",
        filetypes=[
            ("wav files", "*.wav"),
            ("All files", "*.*")
            ]
        )

        Data_path[IDs[i]] = {}
        Data_path[IDs[i]]["Audio"] = aud_path

        if aud_path == (None or ""):
            print("You chose NO file. ")
            i -= 1
            continue

        print(aud_path,"\n\n", "="*28)
        
        temp = data_volt(aud_path)

        #-----------------------------------------#
        #calibration or frequency response correction

        sensitivity_dbv_pa = int(input(" Please enter the sensitivity of the mic.(dBV/Pa) \n"
                               "    >> "))
        print("\n")
        gain = int(input(" Please enter the gain of the circuit. \n"
                     "    >> "))
        print("\n\n")

        temp["sensitivity"] = sensitivity_dbv_pa
        temp["gain"] = gain

        temp["voltage"] = ap.prepre_process(
            temp["voltage"], gain
            )

        ans_cal = input("Do you need calibration or frequency response correction? [y/n]"
            " >> ")

        if ans_cal == "y" : 
            #ask some information about calculating dB SPL
            print("We need frequency response csv file. ")
            resp_path = read_csv()
            Data_path[IDs[i]]["freq_resp"] = resp_path
            temp = temp | align_freq_resp(resp_path)

        #------------------------------------------#
        #Get the USV labels data

        ans_label = input("Do you have USV labels for the data? [y/n] \n"
                    " >> ")
        if ans_label == "y":

            label_path = select_csv()
            Data_path["label"] = label_path
            temp["labels"] = (
                vl.align_label(
                            label_path, temp["sr"]
                            ))
            Data_label[IDs[i]] = temp["labels"]
            #It has labels with [index of onset, index of offset]
            temp["masked_voltage"] = (
                ap.mask_label(temp))

        else : 
            pass
            #refer the code in MSA2

        Data_aud[IDs[i]] = temp

            
    return IDs, Data_path, Data_aud, Data_label

#=================================================#

#store the voltage value and make the list of time
def data_volt(aud_path) : 

    sr, voltage = sc.io.wavfile.read(aud_path)
    time_interval = 1 / sr
    time = list(range(len(voltage)))
    for i in range(len(voltage)) : 
        time[i] = i * time_interval
    audio_info = {
        "sr" : sr, 
        "voltage" : voltage, 
        "time" : time
        }

    return audio_info



#===================================================#



#현재 값이 숫자인지 판별
def is_number(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


#file_path 받아 header 찾기
def find_header_row(file_path):
    with open(file_path, "r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))

    for i in range(len(rows) - 1):
        current_row = rows[i]
        next_row = rows[i + 1]

        # 빈 행 제외
        if not current_row or not next_row:
            continue

        # 현재 행과 다음 행의 열 개수가 같은 경우
        if len(current_row) != len(next_row):
            continue

        # 현재 행에 문자열이 있는지
        current_has_text = any(
            not is_number(value.strip())
            for value in current_row
            if value.strip()
        )

        # 다음 행이 대부분 숫자인지
        next_numeric = sum(
            is_number(value.strip())
            for value in next_row
            if value.strip()
        )

        if current_has_text and next_numeric >= len(next_row) * 0.8:
            return (i, rows[i])
    return None

#============================================#

#define and align the information of response of frequency and dB
def align_freq_resp(resp_path) :

    (i, header_row) = find_header_row(resp_path)

    df= pd.read_csv(resp_path, skiprows = i)
    n=0
    re_hz,re_db=(0,0)

    print(" Check the header and choose the number of header \n"
          " of the response of frequency and dB that you want to analyze. ")
    for j in header_row : 
        print(n,":", j)
        n=n+1

    re_hz, re_db = map(
        int,
        input('Enter like [freqency, dB]\n>> ')
        .strip("[]() ")
        .split(",")
    )

    print("\n\n")

    response_freqs = df[header_row[re_hz]]
    response_db = df[header_row[re_db]]
    response = {
        "response_freqs" : response_freqs, 
        "response_db" : response_db
        }

    return response



#==========================================#

#select and read the file
def select_csv() : 
    print("\n","="*28,"\n   YOUR FILE PATH & NAME  \n","   >> ",end="")
    root = tk.Tk()
    root.withdraw()

    file_path = filedialog.askopenfilename(
    title="CSV 파일 선택",
    filetypes=[
        ("CSV files", "*.csv"),
        ("txt files", "*.txt"),
        ("All files", "*.*")
        ]
    )

    if file_path == None:
        print("You chose NO file. ")
        exit("Please check again and retry the program. ")

    print(file_path,"\n\n", "="*28)

    return file_path
