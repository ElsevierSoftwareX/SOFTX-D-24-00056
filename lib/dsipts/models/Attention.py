
from torch import optim, nn
import torch
import pickle
import pytorch_lightning as pl
from torch.optim.lr_scheduler import StepLR
from .base import QuantileLoss, Base
import math
def get_device():
    return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")



def generate_square_subsequent_mask(dim1: int, dim2: int):
    return torch.triu(torch.ones(dim1, dim2) * float('-inf'), diagonal=1)

class PositionalEncoding(nn.Module):

    def __init__(self, d_model, dropout=0.1, max_len=500):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(p=dropout)

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)


    def forward(self, x):
        return  self.pe[:,:x.size(1), :].repeat(x.shape[0],1,1)


class Attention(Base):


    def __init__(self, channels_past,channels_future,n_embd, num_heads,seq_len,pred_len,dropout,n_layer_encoder,n_layer_decoder,embs,embedding_final,out_channels,quantiles=[],optim_config=None,scheduler_config=None):
        self.save_hyperparameters(logger=False)
        # n_embd: embedding dimension, n_head: the number of heads we'd like
        super().__init__()
        self.pred_len = pred_len
        assert (len(quantiles) ==0) or (len(quantiles)==3)
        if len(quantiles)>0:
            self.use_quantiles = True
        else:
            self.use_quantiles = False
        self.optim_config = optim_config
        self.scheduler_config = scheduler_config

        self.pe = PositionalEncoding( embedding_final, dropout=dropout, max_len=seq_len+pred_len+1)
        self.emb_list = nn.ModuleList()
        if embs is not None:
            for k in embs:
                self.emb_list.append(nn.Embedding(k+1,embedding_final))

        self.initial_layer_decoder = nn.Conv1d(in_channels=len(embs)*embedding_final+embedding_final+channels_future, out_channels= n_embd, kernel_size=1, stride=1,padding='same')
        self.initial_layer_encoder = nn.Conv1d(in_channels=len(embs)*embedding_final+embedding_final+channels_past, out_channels= n_embd, kernel_size=1, stride=1,padding='same')


        encoder_layer = nn.TransformerEncoderLayer( d_model=n_embd, nhead=num_heads, dim_feedforward=n_embd, dropout=dropout,batch_first=True ,norm_first=True)
        self.encoder = nn.TransformerEncoder( encoder_layer=encoder_layer, num_layers=n_layer_encoder, norm=None)
        decoder_layer = nn.TransformerDecoderLayer( d_model=n_embd, nhead=num_heads, dim_feedforward=n_embd, dropout=dropout,batch_first=True ,norm_first=True)
        self.decoder = nn.TransformerDecoder(decoder_layer=decoder_layer,num_layers=n_layer_decoder,norm=None)
        
 
        
        self.final_linear = nn.ModuleList()
        for _ in range(3 if self.use_quantiles else out_channels):
            self.final_linear.append(nn.Sequential(nn.Linear(n_embd,n_embd//2),nn.ReLU(),nn.Linear(n_embd//2,1)))

  
        if  self.use_quantiles:
            self.loss = QuantileLoss(quantiles)
        else:
            self.loss = nn.L1Loss()
        
 
     
        
    def forward(self,batch):
        x_past = batch['x_num_past'].to(self.device)

        tmp = [x_past,self.pe(x_past[:,:,0])]
        if 'x_cat_past' in batch.keys():
            x_cat_past = batch['x_cat_past'].to(self.device)
            for i in range(len(self.emb_list)):
                tmp.append(self.emb_list[i](x_cat_past[:,:,i]))

            
        x = torch.cat(tmp,2)
        ##BS x L x channels
        x = self.initial_layer_encoder( x.permute(0,2,1)).permute(0,2,1)       
        enc_seq_len = x.shape[1]

        src = self.encoder(x)

        ##decoder part
        if 'x_num_future' in batch.keys():
            x_future = batch['x_num_future'].to(self.device)
            tmp = [x_future,self.pe(x_future[:,:,0])]
        else:
            tmp = []
        if 'x_cat_future' in batch.keys():
            x_cat_future = batch['x_cat_future'].to(self.device)
            for i in range(len(self.emb_list)):
                tmp.append(self.emb_list[i](x_cat_future[:,:,i]))
            tmp.append(self.pe(x_cat_future[:,:,i]))
        if len(tmp)==0:
            SystemError('Please give me something for the future')
        y = torch.cat(tmp,2)
        
        y = self.initial_layer_decoder( y.permute(0,2,1)).permute(0,2,1)       
        forecast_window = y.shape[1]
        

      
        
        
        tgt_mask = generate_square_subsequent_mask(
            dim1=forecast_window,
            dim2=forecast_window
            ).to(self.device)

        src_mask = generate_square_subsequent_mask(
            dim1=forecast_window,
            dim2=enc_seq_len
            ).to(self.device)
        
        decoder_output = self.decoder(
            tgt=y,
            memory=src,
            tgt_mask=tgt_mask,
            memory_mask=src_mask
            )

        res = []
        for j in range(len(self.final_linear)):
            res.append(self.final_linear[j](decoder_output))
       
        ##check
        return torch.cat(res,2)
    
    
    
    def inference(self, batch):
        tmp_x_past= batch.get('x_num_past',None)    
        tmp_cat_past= batch.get('x_cat_past',None)
        tmp_x_future= batch.get('x_num_future',None)    
        tmp_cat_future= batch.get('x_cat_future',None)
        
        tmp = {}
        if tmp_x_past is not None:
            tmp_x_past.to(self.device)
            tmp['x_num_past'] = tmp_x_past
        if tmp_cat_past is not None:
            tmp_cat_past.to(self.device)
            tmp['x_cat_past'] = tmp_cat_past
        if tmp_x_future is not None:
            tmp_x_future.to(self.device)
            tmp['x_num_future'] = tmp_x_future[:,0:1,:]
        if tmp_cat_future is not None:
            tmp_cat_future.to(self.device)
            tmp['x_cat_future'] = tmp_cat_future[:,0:1,:]
        ##TODO questo funziona solo senza meteo! 
        
        with torch.set_grad_enabled(False):
            y = []
            count = 0 
            for i in range(self.pred_len):
                y_i = self(tmp)
                ##quantile loss!
                if self.use_quantiles:
                    pred = y_i[:,-1:,1:2]
                else:
                    pred = y_i[:,-1:,:]
                if tmp_x_future is not None:
                    ##TODO questo funziona solo senza meteo! 
                    tmp['x_num_future'] =torch.cat([tmp['x_num_future'].to(self.device),pred],1)
                count+=1
                if tmp_cat_future is not None:
                    tmp['x_cat_future'] = tmp_cat_future[:,0:count+1,:]
                
                y.append(y_i[:,-1:,:])#.detach().cpu().numpy())
            
            return torch.cat(y,1)     