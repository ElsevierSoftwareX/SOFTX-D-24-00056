
import argparse
import pandas as pd
from dsipts import TimeSeries, RNN, Attention,read_public_dataset, LinearTS, Persistent,D3VAE,MyModel, TFT
from omegaconf import DictConfig, OmegaConf
import hydra
import os
import shutil
import numpy as np
import plotly.express as px
from sklearn.metrics import mean_squared_error
from typing import List


def rmse(x:np.array,y:np.array)->float:
    """custom RMSE avoinding nan

    Args:
        x (np.array): predicted
        y (np.array): real

    Returns:
        float: RMSE
    """
    x = x.astype(float)
    y = y.astype(float)
    idx = list(np.where(~np.isnan(x*y))[0])
    return np.sqrt(mean_squared_error(x[idx],y[idx]))

def mse(x:np.array,y:np.array)->float:
    """custom MSE avoinding nan

    Args:
        x (np.array): predicted
        y (np.array): real

    Returns:
        float: MSE
    """
    x = x.astype(float)
    y = y.astype(float)
    idx = list(np.where(~np.isnan(x*y))[0])
    return mean_squared_error(x[idx],y[idx])

def mape(x:np.array,y:np.array)->float:
    """custom mape avoinding nan

    Args:
        x (np.array): predicted
        y (np.array): real

    Returns:
        float: mape
    """
    x = x.astype(float)
    y = y.astype(float)
    idx = list(np.where(~np.isnan(x*y))[0])
    res = 100*np.abs((x[idx]-y[idx])/y[idx])
    res = res[np.isfinite(res)]
    return np.nanmean(res)


def inference(conf:DictConfig)->List[pd.DataFrame]:
    """Make inference on a selected set starting from a configuration file

    Args:
        conf (DictConfig): inference configuration, usually the one generated by the train with all the paths and parameters. See the examples in the repo

    Returns:
        List[pd.DataFrame]:  3 dataframes:
            errors : containing the errors
            res : containing the predictions
            losses : containing the losses during the train
    """
    ##OCCHIO CHE tutti questi dataset hanno y come target! ###############################################
    data, columns = read_public_dataset(**conf.dataset)
    ts = TimeSeries(conf.ts.name)
    ts.load_signal(data, enrich_cat= conf.ts.enrich,target_variables=['y'], past_variables=columns)
    ######################################################################################################
    

    print(f"{''.join(['#']*100)}")
    print(f"{conf.model.type:^100}")  
    print(f"{''.join(['#']*100)}")

    print(f'Model and weights will be placed and read from {conf.train_config.dirpath}')
    

    if conf.model.type=='attention':
        ts.load(Attention,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'linear':
        ts.load(LinearTS,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'rnn':
        ts.load(RNN,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'persistent':
        ts.load(Persistent,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'd3vae':
        ts.load(D3VAE,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'mymodel':
        ts.load(MyModel,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
    elif conf.model.type == 'tft':
        ts.load(TFT,os.path.join(conf.train_config.dirpath,'model'),load_last=conf.inference.load_last)
  
    else:
        print('use a valid model')
    
    res = ts.inference_on_set(batch_size = conf.inference.batch_size,
                                num_workers = conf.inference.num_workers,
                                set = conf.inference.set,
                                rescaling =conf.inference.rescaling)

    errors = []
    feat = '_median' if ts.model.use_quantiles else '_pred'
    for c in ts.target_variables:
        
        tmp = res.groupby('lag').apply(lambda x: mse(x[f'{c}{feat}'].values,x[c].values)).reset_index().rename(columns={0:f'MSE'})
        tmp['variable'] = c
        
        tmp2 = res.groupby('lag').apply(lambda x: mape(x[f'{c}{feat}'].values,x[c].values)).reset_index().rename(columns={0:'MAPE'})
        tmp2['variable'] = c
        errors.append(pd.merge(tmp,tmp2))
    errors = pd.concat(errors,ignore_index=True)
    print(errors)

    if not os.path.exists(os.path.join(conf.inference.output_path,'csv')):
        os.makedirs(os.path.join(conf.inference.output_path,'csv'))
    filename = os.path.join(conf.inference.output_path,'csv',f'{conf.model.type}_{ts.name}_{conf.ts.version}_{conf.inference.set}.csv')

    errors.to_csv(filename,index=False)
    return errors,res, ts.losses


    
if __name__ == '__main__': 
    parser = argparse.ArgumentParser(description="Train TS models")
    parser.add_argument("-c", "--config", type=str, help="configurastion file")
    args = parser.parse_args()
    conf = OmegaConf.load(args.config) 
    inference(conf)
