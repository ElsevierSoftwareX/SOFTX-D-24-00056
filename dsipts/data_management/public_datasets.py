import pandas as pd
import os
import numpy as np


def read_public_dataset(path,dataset):
    '''
    Returns the public dataset chosen. Pleas download the dataset from here https://drive.google.com/drive/folders/1ZOYpTUa82_jCcxIdTmyr0LXQfvaM9vIy or ask to agobbi@fbk.eu. 
    Extract the data and leave the name all_six_datasets in the path folder

        Parameters:
                path (str): path to data
                dataset (str): dataset (one of 'electricity','etth1','etth2','ettm1','ettm2','exchange_rate','illness','traffic','weather')

        Returns:
                dataset (pandas.dataset): dataset. The target variable is *y* and the time index is *time*
                covariates (list): list of past covariates
    '''
    if os.path.isdir(path):
        pass
    else:
        print('I will try to create the folder')
        os.mkdir(path)
        
    files = os.listdir(path)
    if 'all_six_datasets' in files:
        pass
    else:
        print('Please dowload the zip file form here and unzip it https://drive.google.com/drive/folders/1ZOYpTUa82_jCcxIdTmyr0LXQfvaM9vIy')
        return None,None
    
    
    if dataset not in ['electricity','etth1','etth2','ettm1','ettm2','exchange_rate','illness','traffic','weather']:
        print(f'Dataset {dataset} not available')
        return None,None

    if dataset=='electricity':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/electricity/electricity.csv'),sep=',',na_values=-9999)
    elif dataset=='etth1':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/ETT-small/ETTh1.csv'),sep=',',na_values=-9999)  
    elif dataset=='etth1':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/ETT-small/ETTh2.csv'),sep=',',na_values=-9999)
    elif dataset=='ettm1':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/ETT-small/ETTm1.csv'),sep=',',na_values=-9999)
    elif dataset=='ettm2':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/ETT-small/ETTm2.csv'),sep=',',na_values=-9999)
    elif dataset=='exchange_rate':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/exchange_rate/exchange_rate.csv'),sep=',',na_values=-9999)
    elif dataset=='exchange_rate':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/illness/national_illness.csv'),sep=',',na_values=-9999)
    elif dataset=='traffic':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/traffic/traffic.csv'),sep=',',na_values=-9999) 
    elif dataset=='weather':
        dataset = pd.read_csv(os.path.join(path,'all_six_datasets/weather/weather.csv'),sep=',',na_values=-9999) 
    else:
        print(f'Dataset {dataset} not found')
        return None, None
    dataset.rename(columns={'date':'time','OT':'y'},inplace=True)
    dataset.time = pd.to_datetime(dataset.time)
    print(f'Dataset loaded with shape {dataset.shape}')
    
    return dataset, list(set(dataset.columns).difference(set(['time','y'])))