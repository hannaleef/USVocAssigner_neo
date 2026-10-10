#Use MSA2 later...
import data_load as dl
import aud_process as ap

import pandas as pd


def align_label(label_path, sr) :
    (i, header_row) = dl.find_header_row(label_path)
    df = pd.read_csv(label_path, skiprows = i)
    n=0

    print(" Check the header and choose the number of header \n"
            " of the response of onset and offset that you want to analyze. ")
    for j in header_row :
        print(n,":", j)
        n=n+1

    on, off = map(
        int,
        input('Enter like [onset, offset]\n>> ')
        .strip("[]() ")
        .split(",")
    )

    print("\n\n")

    onsets = df[header_row[on]]
    offsets = df[header_row[off]]
    labels_idx = []

    labels_idx = ap.extract_usv(sr, onsets, offsets)

    return labels_idx