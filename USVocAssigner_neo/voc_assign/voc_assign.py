def arrange_assignment(audio_dataset, video_dataset, hop_length) : 
	assign_dataset = []

	for audio_data1 in audio_dataset : 
		assign_dataset.append(AssignData(audio_data1, audio_datset, hop_length))
		
	for i in range(len(assign_dataset)) : 
		assign_data[i].dist_corr(audio_dataset[i], audio_dataset, video_dataset, hop_length)
		assign_data[i].geo_corr(audio_dataset[i], audio_dataset, video_datset, hop_length)
	
	return assign_dataset


class AssignData 
	def __init__(self, audio_data1, audio_dataset, hop_length) : 
		self.ID, self.label_idx, self.assign_origin = (
			ratio_only_assignment(audio_data1, audio_datset, hop_length)
			)
	def dist_corr(self, audio_data1, audio_dataset, video_dataset, hop_length) : 
		self.assign_dist_corr = ()
	def geo_corr(self, audio_data1, audio_dataset, video_datset, hop_length) : 
		self.assign_geo_corr = ()


def ratio_only_assignment(audio_data1, audio_dataset, hop_length) :

  USVs = {}
  USV_meds = {}
  amplitude = {}
  
  ID1 = audio_data1.ID
  assign_origin = []
  
  for label in audio_data1.label_idx :
    onset_idx = label[0] // hop_length
    offset_idx = label[1] // hop_length

    for audio_data2 in audio_dataset :
      ID2 = audio_data2.ID
      if ID2 != ID1 : 
        amplitude[ID2] = audio_data2.amplitude
        USVs[ID2] = amplitude[ID2][:, onset_idx:offset_idx]
        USV_meds[ID2] = np.median(USVs[ID2])
                    
     else : 
       continue
       
	   ratio = 0
	   max_amp = max(USV_meds.values())
	
	   winners = [
	     ID for ID, amp in USV_meds.items()
	     if amp == max_amp
		   ]
	
	   if len(winners) == 1:
       assign = winners[0]
	   else:
       assign = "unassigned"
         
	   assign_origin.append(assign)
           
   return ID1, audio_data.label_idx, assign_origin


def distance_corr_assignment() : pass


def geo_corr_assignment() : pass