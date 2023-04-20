
from torch import  nn
import torch
from .base import Base
from .utils import QuantileLossMO,Permute, get_device,L1Loss, get_activation
from typing import List
import numpy as np

class Block(nn.Module):
    def __init__(self,input_channels:int,kernel_sie:int,output_channels:int,input_size:int,sum_layers:bool ):
    
    
        super(Block, self).__init__()

        self.dilations = nn.ModuleList()
        self.steps = int(np.floor(np.sqrt(input_size)))
        for i in range(self.steps):
            self.dilations.append(nn.Conv1d(input_channels, output_channels, kernel_sie, stride=1,padding='same',dilation=2**i))
        self.sum_layers = sum_layers
        mul = 1 if sum_layers else self.steps 
        self.conv_final = nn.Conv1d(output_channels*mul, output_channels, kernel_sie, stride=1,padding='same')

    def forward(self, x: torch.tensor) -> torch.tensor:
        x = Permute()(x)
        tmp = []
        for i in range(self.steps):
            tmp.append(self.dilations[i](x))

        if self.sum_layers:
            tmp = torch.stack(tmp)
            tmp = tmp.sum(axis=0)
        else:
            tmp = torch.cat(tmp,1)
        
        return Permute()(tmp)
        
        

class MyModel(Base):

    
    def __init__(self, 
                 past_steps:int,
                 future_steps:int,
                 past_channels:int,
                 future_channels:int,
                 embs:List[int],
                 cat_emb_dim:int,
                 hidden_RNN:int,
                 num_layers_RNN:int,
                 kind:str,
                 kernel_size_encoder:int,
                 sum_emb:bool,
                 out_channels:int,
                 persistence_weight:float,
                 activation:str='relu',
                 quantiles:List[int]=[],
                 dropout_rate:float=0.1,
                 use_bn:bool=False,
                 optim_config:dict=None,
                 scheduler_config:dict=None)->None:
        """ Custom encoder-decoder 

        Args:
            past_steps (int):  number of past datapoints used 
            future_steps (int): number of future lag to predict
            past_channels (int): number of numeric past variables, must be >0
            future_channels (int): number of future numeric variables 
            embs (List): list of the initial dimension of the categorical variables
            cat_emb_dim (int): final dimension of each categorical variable
            hidden_RNN (int): hidden size of the RNN block
            num_layers_RNN (int): number of RNN layers
            kind (str): one among GRU or LSTM
            kernel_size_encoder (int): kernel size in the encoder convolutional block
            sum_emb (bool): if true the contribution of each embedding will be summed-up otherwise stacked
            out_channels (int):  number of output channels
            persistence_weight (float):  weight controlling the divergence from persistence model
            activation (str, optional): activation fuction
            quantiles (List[int], optional): we can use quantile loss il len(quantiles) = 0 (usually 0.1,0.5, 0.9) or L1loss in case len(quantiles)==0. Defaults to [].
            dropout_rate (float, optional): dropout rate in Dropout layers
            use_bn (bool, optional): if true BN layers will be added and dropouts will be removed
            optim_config (dict, optional): configuration for Adam optimizer. Defaults to None.
            scheduler_config (dict, optional): configuration for stepLR scheduler. Defaults to None.

        """
        if activation == 'SELU':
            print('SELU do not require BN')
            use_bn = False
        activation = get_activation(activation)
        
        super(MyModel, self).__init__()
        self.save_hyperparameters(logger=False)
        #self.device = get_device()
        self.past_steps = past_steps
        self.future_steps = future_steps
        self.persistence_weight = persistence_weight 
        self.num_layers_RNN = num_layers_RNN
        self.hidden_RNN = hidden_RNN
        self.past_channels = past_channels 
        self.future_channels = future_channels 
        self.embs = nn.ModuleList()
        self.sum_emb = sum_emb
        self.kind = kind
        self.out_channels = out_channels
        assert (len(quantiles) ==0) or (len(quantiles)==3)
        if len(quantiles)>0:
            self.use_quantiles = True
            self.mul = 3
        else:
            self.use_quantiles = False
            self.mul = 1
        
        emb_channels = 0
        self.optim_config = optim_config
        self.scheduler_config = scheduler_config

        for k in embs:
            self.embs.append(nn.Embedding(k+1,cat_emb_dim))
            emb_channels+=cat_emb_dim
            
            
        if sum_emb and (emb_channels>0):
            emb_channels = cat_emb_dim
            print('Using sum')
        else:
            print('Using stacked')
    
        self.initial_linear_encoder =  nn.Sequential(Permute(),
                                                    nn.Conv1d(past_channels, (past_channels+hidden_RNN//8)//2, kernel_size_encoder, stride=1,padding='same'),
                                                    activation(),
                                                    nn.BatchNorm1d(  (past_channels+hidden_RNN//8)//2) if use_bn else nn.Dropout(dropout_rate) ,
                                                    nn.Conv1d( (past_channels+hidden_RNN//8)//2, hidden_RNN//8, kernel_size_encoder, stride=1,padding='same'),
                                                    Permute())

        self.initial_linear_decoder =   nn.Sequential(Permute(),
                                                    nn.Conv1d(future_channels, (future_channels+hidden_RNN//8)//2, kernel_size_encoder, stride=1,padding='same'),
                                                    activation(),
                                                    nn.BatchNorm1d(  (future_channels+hidden_RNN//8)//2) if use_bn else nn.Dropout(dropout_rate) ,
                                                    nn.Conv1d( (future_channels+hidden_RNN//8)//2, hidden_RNN//8, kernel_size_encoder, stride=1,padding='same'),
                                                    Permute())
        self.conv_encoder = Block(emb_channels+hidden_RNN//8,kernel_size_encoder,hidden_RNN//4,self.past_steps,sum_emb)
        
        #nn.Sequential(Permute(), nn.Conv1d(emb_channels+hidden_RNN//8, hidden_RNN//8, kernel_size_encoder, stride=1,padding='same'),Permute(),nn.Dropout(0.3))
        #import pdb
        #pdb.set_trace()
        if future_channels+emb_channels==0:
            ## occhio che vuol dire che non ho passato , per ora ci metto una pezza e uso hidden dell'encoder
            self.conv_decoder = Block(hidden_RNN//2,kernel_size_encoder,hidden_RNN//4,self.future_steps,sum_emb) 
        else:
            self.conv_decoder = Block(future_channels+emb_channels,kernel_size_encoder,hidden_RNN//4,self.future_steps,sum_emb) 
            #nn.Sequential(Permute(),nn.Linear(past_steps,past_steps*2),  nn.PReLU(),nn.Dropout(0.2),nn.Linear(past_steps*2, future_steps),nn.Dropout(0.3),nn.Conv1d(hidden_RNN, hidden_RNN//8, 3, stride=1,padding='same'),   Permute())
        if self.kind=='lstm':
            self.Encoder = nn.LSTM(input_size= hidden_RNN//4,
                                   hidden_size=hidden_RNN//4,
                                   num_layers = num_layers_RNN,
                                   batch_first=True,bidirectional=True)
            self.Decoder = nn.LSTM(input_size= hidden_RNN//4,
                                   hidden_size=hidden_RNN//4,
                                   num_layers = num_layers_RNN,
                                   batch_first=True,bidirectional=True)
        elif self.kind=='gru':
            self.Encoder = nn.GRU(input_size= hidden_RNN//4,
                                  hidden_size=hidden_RNN//4,
                                  num_layers = num_layers_RNN,
                                  batch_first=True,bidirectional=True)
            self.Decoder = nn.GRU(input_size= hidden_RNN//4,
                                  hidden_size=hidden_RNN//4,
                                  num_layers = num_layers_RNN,
                                  batch_first=True,bidirectional=True)
        else:
            print('Speciky kind= lstm or gru please')
        self.final_linear = nn.ModuleList()
        for _ in range(out_channels*self.mul*self.future_steps):
            self.final_linear.append(nn.Sequential(nn.Linear(hidden_RNN//2+emb_channels,hidden_RNN//4), 
                                            activation(),
                                            nn.BatchNorm1d(hidden_RNN//4) if use_bn else nn.Dropout(dropout_rate) ,
                                            nn.Linear(hidden_RNN//4,hidden_RNN//8),
                                            activation(),
                                            nn.BatchNorm1d(hidden_RNN//8) if use_bn else nn.Dropout(dropout_rate) ,
                                            nn.Linear(hidden_RNN//8,hidden_RNN//16),
                                            activation(),
                                            nn.Dropout(dropout_rate),
                                            nn.Linear(hidden_RNN//16,1)))

  
        if  self.use_quantiles:
            self.loss = QuantileLossMO(quantiles)
        else:
            self.loss = L1Loss()
        #self.device = get_device()
        
        
    def training_step(self, batch, batch_idx):
        """
        pythotrch lightening stuff
        
        :meta private:
        """
        y_hat = self(batch)
        
        mse_loss = self.loss(y_hat, batch['y'])
        x =  batch['x_num_past'].to(self.device)
        idx_target = batch['idx_target'][0]
        x_start = x[:,-1,idx_target].unsqueeze(1)
        y_persistence = x_start.repeat(1,self.future_steps,1)
        
        
        idx = 1 if self.use_quantiles else 0
        persistence_loss = -nn.L1Loss()(y_persistence,y_hat[:,:,:,idx])
        loss = self.persistence_weight*mse_loss + (1-self.persistence_weight)*persistence_loss
        return loss
    
    def validation_step(self, batch, batch_idx):
        """
        pythotrch lightening stuff
        
        :meta private:
        """
        y_hat = self(batch)
        
        mse_loss = self.loss(y_hat, batch['y'])
        x =  batch['x_num_past'].to(self.device)
        idx_target = batch['idx_target'][0]
        x_start = x[:,-1,idx_target].unsqueeze(1)
        y_persistence = x_start.repeat(1,self.future_steps,1)
        
        
        idx = 1 if self.use_quantiles else 0
        persistence_loss = -nn.L1Loss()(y_persistence,y_hat[:,:,:,idx])
        loss = self.persistence_weight*mse_loss + (1-self.persistence_weight)*persistence_loss
        return loss
    
    def forward(self, batch):
        """It is mandatory to implement this method

        Args:
            batch (dict): batch of the dataloader

        Returns:
            torch.tensor: result
        """
        x =  batch['x_num_past'].to(self.device)

        if 'x_cat_future' in batch.keys():
            cat_future = batch['x_cat_future'].to(self.device)
        if 'x_cat_past' in batch.keys():
            cat_past = batch['x_cat_past'].to(self.device)
        if 'x_num_future' in batch.keys():
            x_future = batch['x_num_past'].to(self.device)
        else:
            x_future = None  
        tmp = [self.initial_linear_encoder(x)]
        
        for i in range(len(self.embs)):
            if self.sum_emb:
                if i>0:
                    tmp_emb+=self.embs[i](cat_past[:,:,i])
                else:
                    tmp_emb=self.embs[i](cat_past[:,:,i])
            else:
                tmp.append(self.embs[i](cat_past[:,:,i]))
        if self.sum_emb and (len(self.embs)>0):
            tmp.append(tmp_emb)
        tot = torch.cat(tmp,2)

        out, hidden = self.Encoder(self.conv_encoder(tot))      

        tmp = []
        for i in range(len(self.embs)):
            if self.sum_emb:
                if i>0:
                    tmp_emb+=self.embs[i](cat_future[:,:,i])
                else:
                    tmp_emb=self.embs[i](cat_future[:,:,i])
            else:
                tmp.append(self.embs[i](cat_future[:,:,i]))   
        if self.sum_emb and (len(self.embs)):
            tmp.append(tmp_emb)
            
        if x_future is not None:
            tmp.append(x_future)

        if len(tmp)>0:
            tot = torch.cat(tmp,2)
            out, _ = self.Decoder(self.conv_decoder(tot),hidden)  
            has_future = True
        else:
            out, _ = self.Decoder(self.conv_decoder(out),hidden)  
            has_future = False
        res = []


        for i in range(self.future_steps):
            if has_future:
                tmp = torch.cat([tot[:,i,:],out[:,i,:]],axis=1)
            else:
                tmp = out[:,i,:]
            for j in range(self.out_channels*self.mul):
                res.append(self.final_linear[j](tmp))

        res = torch.cat(res,1)
        ##BxLxC
        B = res.shape[0]
        
      
        return res.reshape(B,self.future_steps,-1,self.mul)

    