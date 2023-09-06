import torch
import torch.nn as nn

class embedding_cat_variables(nn.Module):
    # at the moment cat_past and cat_fut together
    def __init__(self, seq_len: int, lag: int, d_model: int, emb_dims: list, device):
        """Class for embedding categorical variables, adding 3 positional variables during forward

        Args:
            seq_len (int): length of the sequence (sum of past and future steps)
            lag (int): number of future step to be predicted
            hiden_size (int): dimension of all variables after they are embedded
            emb_dims (list): size of the dictionary for embedding. One dimension for each categorical variable
            device : -
        """
        super().__init__()
        self.seq_len = seq_len
        self.lag = lag
        self.device = device
        self.cat_embeds = emb_dims + [seq_len, lag+1, 2] # 
        self.cat_n_embd = nn.ModuleList([
            nn.Embedding(emb_dim, d_model) for emb_dim in self.cat_embeds
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """All components of x are concatenated with 3 new variables for data augmentation, in the order:
        - pos_seq: assign at each step its time-position
        - pos_fut: assign at each step its future position. 0 if it is a past step
        - is_fut: explicit for each step if it is a future(1) or past one(0)

        Args:
            x (torch.Tensor): [bs, seq_len, num_vars]

        Returns:
            torch.Tensor: [bs, seq_len, num_vars+3, n_embd] 
        """
        if len(x.shape)==0:
            no_emb = True
            B = x.item()
        else:
            no_emb = False
            B, _, _ = x.shape
        
        pos_seq = self.get_pos_seq(bs=B).to(x.device)
        pos_fut = self.get_pos_fut(bs=B).to(x.device)
        is_fut = self.get_is_fut(bs=B).to(x.device)
        if no_emb:
            cat_vars = torch.cat((pos_seq, pos_fut, is_fut), dim=2)
        else:
            cat_vars = torch.cat((x, pos_seq, pos_fut, is_fut), dim=2)
        cat_n_embd = self.get_cat_n_embd(cat_vars)
        return cat_n_embd

    def get_pos_seq(self, bs):
        pos_seq = torch.arange(0, self.seq_len)
        pos_seq = pos_seq.repeat(bs,1).unsqueeze(2).to(self.device)
        return pos_seq
    
    def get_pos_fut(self, bs):
        pos_fut = torch.cat((torch.zeros((self.seq_len-self.lag), dtype=torch.long),torch.arange(1,self.lag+1)))
        pos_fut = pos_fut.repeat(bs,1).unsqueeze(2).to(self.device)
        return pos_fut
    
    def get_is_fut(self, bs):
        is_fut = torch.cat((torch.zeros((self.seq_len-self.lag), dtype=torch.long),torch.ones((self.lag), dtype=torch.long)))
        is_fut = is_fut.repeat(bs,1).unsqueeze(2).to(self.device)
        return is_fut
    
    def get_cat_n_embd(self, cat_vars):
        cat_n_embd = torch.Tensor().to(cat_vars.device)
        for index, layer in enumerate(self.cat_n_embd):
            emb = layer(cat_vars[:, :, index])
            cat_n_embd = torch.cat((cat_n_embd, emb.unsqueeze(2)),dim=2)
        return cat_n_embd

class LSTM_Model(nn.Module):
    def __init__(self, num_var: int, d_model: int, pred_step: int, num_layers: int, dropout: float):
        super().__init__()
        self.num_var = num_var
        self.d_model = d_model
        self.num_layers = num_layers
        self.pred_step = pred_step

        self.lstm = nn.LSTM(d_model, d_model, num_layers=num_layers, batch_first=True, dropout=dropout)
        self.linear = nn.Linear(d_model, pred_step*num_var)

    def forward(self, x):
        h0 = torch.zeros(self.num_layers, x.size(0), self.d_model).to(x.device)
        c0 = torch.zeros(self.num_layers, x.size(0), self.d_model).to(x.device)
        out, _ = self.lstm(x, (h0, c0))
        out = self.linear(out[:, -1, :])  # Take the last output of the sequence
        out = out.view(-1, self.pred_step, self.num_var)
        return out
    
class GLU(nn.Module):
    def __init__(self, d_model: int):
        super().__init__()
        self.linear1 = nn.Linear(d_model, d_model, bias = False)
        self.linear2 = nn.Linear(d_model, d_model, bias = False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1 = self.sigmoid(self.linear1(x))
        x2 = self.linear2(x)
        out = x1*x2 #element-wise multiplication
        return out
    
class GRN(nn.Module):
    def __init__(self, d_model: int, dropout_rate: float):
        super().__init__()
        self.linear1 = nn.Linear(d_model, d_model) 
        self.elu = nn.ELU()
        self.linear2 = nn.Linear(d_model, d_model)
        self.res_conn = ResidualConnection(d_model, dropout_rate)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        eta1 = self.elu(self.linear1(x))
        eta2 = self.linear2(eta1)
        out = self.res_conn(eta2, x)
        return out
    
class ResidualConnection(nn.Module):
    def __init__(self, d_model, dropout_rate) -> None:
        super().__init__()
        self.dropout = nn.Dropout(dropout_rate)
        self.glu = GLU(d_model)
        self.norm = nn.LayerNorm(d_model)
    
    def forward(self, x: torch.Tensor, res_conn: torch.Tensor) -> torch.Tensor:
        x = self.glu(self.dropout(x))
        out = self.norm(res_conn + x)
        return out

class InterpretableMultiHead(nn.Module):
    def __init__(self, d_model, d_head, n_head) -> None:
        super().__init__()
        self.d_head = d_head
        self.n_head = n_head
        self.Q_layers = nn.ModuleList([nn.Linear(d_model,d_head) for _ in range(n_head)])
        self.K_layers = nn.ModuleList([nn.Linear(d_model,d_head) for _ in range(n_head)])
        self.Softmax_layers = nn.ModuleList([nn.Softmax(dim=-1) for _ in range(n_head)])
        self.V_layer = nn.Linear(d_model, d_head)
        self.out_layer = nn.Linear(d_head, d_model)

    def forward(self, query:torch.Tensor, key:torch.Tensor, value:torch.Tensor) -> torch.Tensor:
        out = torch.Tensor()
        for (q_layer, k_layer, softmax) in zip(self.Q_layers, self.K_layers, self.Softmax_layers):
            Q = q_layer(query)
            K = k_layer(key)
            wei = Q @ K.transpose(-2,-1) * (self.d_head**-0.5)
            wei = softmax(wei)
            V = self.V_layer(value)
            out_h = wei @ V
            if out.shape[0]>0:
                out = out + out_h # sum the result of the head attention
            else:
                out = out_h # out is not modifies/initialized yet
        out = out / self.n_head
        out = self.out_layer(out) # comeback to d_model dimension
        return out        