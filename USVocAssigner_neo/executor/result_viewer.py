
def view_result(IDs, Data_aud, hop_length) : 
    for ID in IDs : 
        print(f"ID : {ID} \n\n"
               "==========================\n")
        print("labeled_USV : \n")
        print("index_label, assignment")
        for i in range(len(Data_aud[ID]["labels"])) : 
            time_onset = Data_aud[ID]['labels'][i][0] / Data_aud[ID]['sr']
            time_offset = Data_aud[ID]['labels'][i][1] / Data_aud[ID]['sr']
            time_label = [time_onset, time_offset]
            print(f"{time_label}, {Data_aud[ID]['label_assign'][i]}")
        print("\n\n")
        print("===========================\n")