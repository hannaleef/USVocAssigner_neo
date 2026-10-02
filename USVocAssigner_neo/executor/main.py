#Defalt modules

#Custom modules
import audio.data_load as dl
import audio.aud_process as ap
import audio.voc_label as vl
import voc_assign.voc_assign as va
import executor.result_viewer as rv


num_mic = int(input("Please enter the number of the microphone channels. \n"
                    " >> "))
IDs, Data_path, Data_aud, Data_label = dl.get_info(num_mic)
n_fft, hop_length = ap.set_stft()
Data_aud = ap.do_stft(IDs, Data_aud, n_fft, hop_length)
Data_aud = ap.get_amplitude(IDs, Data_aud)
Data_aud = va.ratio_only_assignment(IDs, Data_aud, Data_label, hop_length)
