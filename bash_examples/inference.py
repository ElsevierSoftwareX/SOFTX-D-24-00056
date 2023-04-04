
import argparse
import pandas as pd
from dsipts import TimeSeries, RNN, Attention,read_public_dataset, LinearTS
from omegaconf import DictConfig, OmegaConf
import hydra
import os
import shutil
import numpy as np
import plotly.express as px
from sklearn.metrics import mean_squared_error

def rmse(x,y):
    x = x.astype(float)
    y = y.astype(float)
    idx = list(np.where(~np.isnan(x*y))[0])
    return np.sqrt(mean_squared_error(x[idx],y[idx]))

def mse(x,y):
    x = x.astype(float)
    y = y.astype(float)
    idx = list(np.where(~np.isnan(x*y))[0])
    return mean_squared_error(x[idx],y[idx])

def mape(x,y):
    x = x.astype(float)
    y = y.astype(float)
    idx = list(np.where(~np.isnan(x*y))[0])
    res = 100*np.abs(x[idx]-y[idx])/y[idx]
    res = res[np.isfinite(res)]
    return np.nanmean(res)

 
def inference(conf):
    ##OCCHIO CHE tutti questi dataset hanno y come target! ###############################################
    data, columns = read_public_dataset(**conf.dataset)
    ts = TimeSeries(conf.ts.name)
    ts.load_signal(data, enrich_cat= conf.ts.enrich,target_variables=['y'], past_variables=columns)
    ######################################################################################################
    



    print(f'Model and weights will be placed and read from {conf.train_config.dirpath}')
    

    if conf.model.type=='attention':
        ts.load(Attention,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'linear':
        ts.load(LinearTS,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'rnn':
        ts.load(RNN,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    else:
        print('use a valid model')
    
    res = ts.inference_on_set(batch_size = conf.inference.batch_size,
                                num_workers = conf.inference.num_workers,
                                set = conf.inference.set)

    errors = []
    for c in ts.target_variables:
        
        tmp = res.groupby('lag').apply(lambda x: mse(x[f'{c}_median'].values,x[c].values)).reset_index().rename(columns={0:f'MSE'})
        tmp['variable'] = c
        
        tmp2 = res.groupby('lag').apply(lambda x: mape(x[f'{c}_median'].values,x[c].values)).reset_index().rename(columns={0:'MAPE'})
        tmp2['variable'] = c
        errors.append(pd.merge(tmp,tmp2))
    errors = pd.concat(errors,ignore_index=True)
    print(errors)
    filename = os.path.join(conf.inference.output_path,f'{conf.model.type}_{ts.name}_{conf.ts.version}_{conf.inference.set}.csv')

    errors.to_csv(filename,index=False)
    return errors,res, ts.losses


    
if __name__ == '__main__': 
    parser = argparse.ArgumentParser(description="Train TS models")
    parser.add_argument("-c", "--config", type=str, help="configurastion file")
    args = parser.parse_args()
    conf = OmegaConf.load(args.config) 
    inference(conf)
