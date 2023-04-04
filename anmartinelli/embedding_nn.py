import torch.nn as nn
import torch

class embedding_cat_variables(nn.Module):
    # at the moment cat_past and cat_fut together
    def __init__(self, seq_len: int, lag: int, d_model: int, emb_dims: list, device):
        """Class for embedding categorical variables

        Args:
            seq_len (int): length of the sequence (sum of past and future steps)
            lag (int): number of future step to be predicted
            d_model (int): dimension of all variables after they are embedded
            emb_dims (list): size of the dictionary for embedding. One dimension for each categorical variable
            device : device for computations
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
        """All conponents of x are concatenated with 3 new variables for data augmentation, in the order:
        - pos_seq: assign at each step its time-position
        - pos_fut: assign at each step its future position. 0 if it is a past step
        - is_fut: explicit for each step if it is a future(1) or past one(0)

        Args:
            x (torch.Tensor): [bs, seq_len, num_vars]

        Returns:
            torch.Tensor: [bs, seq_len num_vars+3, n_embd] 
        """
        B, _, _ = x.shape
        pos_seq = self.get_pos_seq(bs=B)
        pos_fut = self.get_pos_fut(bs=B)
        is_fut = self.get_is_fut(bs=B)
        cat_vars = torch.cat((x, pos_seq, pos_fut, is_fut),dim=2)
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
        cat_n_embd = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.cat_n_embd):
            emb = layer(cat_vars[:, :, index])
            cat_n_embd = torch.cat((cat_n_embd, emb.unsqueeze(2)),dim=2)
        return cat_n_embd
    
class embedding_target(nn.Module):
    def __init__(self, d_model: int):
        """Class for embedding target variable (Only one)

        Args:
            d_model (int): dimension of 
        """
        super().__init__()
        self.y_lin = nn.Linear(1, d_model, bias = False)

    def forward(self, y: torch.Tensor) -> torch.Tensor:
        """Embedding the target varible. (Only one)

        Args:
            y (torch.Tensor): [bs, seq_len, 1] past and future steps of scaled target variable

        Returns:
            torch.Tensor: [bs, seq_len, d_model]
        """
        y = self.y_lin(y.float())
        return y

class GLU(nn.Module):
    # sub net of GRN 
    def __init__(self, n_embd: int) :
        super().__init__()
        self.linear1 = nn.Linear(n_embd, n_embd)
        self.linear2 = nn.Linear(n_embd, n_embd)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor):
        '''
        TFT net called 'Gate' in the paper\n
        No change of dimensions
        '''
        x1 = self.sigmoid(self.linear1(x))
        x2 = self.linear2(x)
        out = x1*x2 #element-wise multiplication
        return out
    
class GRN(nn.Module):
    def __init__(self, n_embd, dropout) :
        super().__init__()
        self.linear1 = nn.Linear(n_embd, n_embd) 
        self.elu = nn.ELU()
        self.linear2 = nn.Linear(n_embd, n_embd)
        self.dropout = nn.Dropout(dropout)
        self.glu = GLU(n_embd)
        self.norm = nn.LayerNorm(n_embd)

    def forward(self, x):
        '''
        Gated Residual Network of TFT\n
        No change of dimensions
        '''
        eta1 = self.elu(self.linear1(x))
        eta2 = self.dropout(self.linear2(eta1))
        out = self.norm(x + self.glu(eta2))
        return out

class flatten_GRN(nn.Module):
    def __init__(self, emb_dims, dropout):
        super().__init__()
        # flatten, hidden dim, output_dim=n_var -> softmax

        # expected the list emb_dims of length 3: 
        # - n_embd
        # - intermediate_dim
        # - number of different variables flattened
        start_emb, mid_emb, end_emb = emb_dims
        self.res_conn = nn.Linear(start_emb, end_emb, bias = False)
        self.dropout_res_conn = nn.Dropout(dropout)
        self.linear1 = nn.Linear(start_emb, mid_emb, bias = False) 
        self.elu = nn.ELU()
        self.linear2 = nn.Linear(mid_emb, end_emb, bias = False)
        self.dropout = nn.Dropout(dropout)
        self.glu = GLU(end_emb)
        self.norm = nn.LayerNorm(end_emb)
        self.softmax = nn.Softmax(dim=2)

    def forward(self, x):
        """
        Part of Variable Selection producing Variable Selection Weights
        Input: categorical, y=None

        If y!=None means I'm using y in th epast and it is added(cat) and selected

        [bs, past_len, n_var, n_embd] -> torch.Size([bs, past_len, n_var])
        """
        res_conn = self.dropout_res_conn(self.res_conn(x))
        eta1 = self.elu(self.linear1(x))
        eta2 = self.dropout(self.linear2(eta1))
        out = self.norm(res_conn + self.glu(eta2))
        out = self.softmax(out)
        return out

class Encoder_Var_Selection(nn.Module): # input already embedded
    def __init__(self, mix, seq_len, lag, n_cat_var, n_num_var, n_embd, dropout, device):
        """_summary_

        Args:
            mix (boolean): _description_
            seq_len int: daje
            lag (int): desc
            n_cat_var (_type_): _description_
            n_num_var (_type_): _description_
            n_embd (_type_): _description_
            dropout (_type_): _description_
            device (_type_): _description_
        """
        super().__init__()
        self.mix = mix
        self.device = device
        self.len = seq_len-lag
        #categorical
        self.n_grn_cat = n_cat_var
        self.GRNs_cat = nn.ModuleList([
            GRN(n_embd, dropout) for _ in range(self.n_grn_cat)
        ])
        tot_var = n_cat_var
        #numerical
        if mix:
            self.n_grn_num = n_num_var
            self.GRNs_num = nn.ModuleList([
                GRN(n_embd, dropout) for _ in range(self.n_grn_num)
            ])
            tot_var = tot_var + n_num_var
        #flatten
        emb_dims = [n_embd*tot_var, int((n_embd+tot_var)/2), tot_var]
        self.flatten_GRN = flatten_GRN(emb_dims, dropout)

    def forward(self, categorical, y=None):
        """
        *var_sel for GRNed variables\n
        'to_be_flat' for VariableSelection Weights\n
        Computed for cat_vars\n
        Concatenating y if y is not None
        """
        # categorical var_selection
        var_sel = self.get_cat_GRN(categorical)
        to_be_flat = categorical
        if y is not None:
            assert self.mix==True # you don't have y if mix is not True
            num_var_sel = self.get_num_GRN(y)
            var_sel = torch.cat((var_sel, num_var_sel), dim = 2)
            to_be_flat = torch.cat((to_be_flat, y), dim=2)

        var_sel_wei = self.get_flat_GRN(to_be_flat)
        out = var_sel*var_sel_wei.unsqueeze(3)
        out = torch.sum(out, 2)/out.shape[2]
        return out

    def get_cat_GRN(self, x):
        cat_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_cat):
            grn = layer(x[:,:,index,:])
            cat_after_GRN = torch.cat((cat_after_GRN, grn.unsqueeze(2)), dim=2)
        return cat_after_GRN
    
    def get_num_GRN(self, x):
        num_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_num):
            grn = layer(x[:,:,index,:])
            num_after_GRN = torch.cat((num_after_GRN, grn.unsqueeze(2)), dim=2)
        return num_after_GRN
    
    def get_flat_GRN(self, to_be_flat):
        emb = torch.flatten(to_be_flat, start_dim=2) # [bs, seq_len, num_var*n_embd]
        var_sel_wei = self.flatten_GRN(emb)
        return var_sel_wei
    
class Encoder_LSTM(nn.Module):
    def __init__(self, n_layers, n_embd, dropout, device) :
        super().__init__()
        self.device = device
        self.num_layers = n_layers
        self.hidden_size = n_embd
        self.LSTM = nn.LSTM(input_size=n_embd, hidden_size=self.hidden_size, num_layers=self.num_layers, batch_first = True)
        self.dropout = nn.Dropout(dropout)
        self.LSTM_enc_GLU = GLU(n_embd)
        self.norm = nn.LayerNorm(n_embd)

    def forward(self, x):
        '''
        After Variable Selection, its output goes through:
         - Lstm_Enc+GLU+Add+Norm\n
        Return output and the last hn and cn that must be used in LSTM_Dec
        '''
        h0 = torch.zeros(self.num_layers, x.size(0), x.size(2)).to(self.device)
        c0 = torch.zeros(self.num_layers, x.size(0), x.size(2)).to(self.device)
        lstm_enc, (hn, cn) = self.LSTM(x, (h0,c0))
        lstm_enc = self.dropout(lstm_enc)
        output_enc = self.norm(self.LSTM_enc_GLU(lstm_enc) + x)
        return output_enc, hn, cn
    
class Decoder_Var_Selection(nn.Module): # input already embedded
    def __init__(self, prec, seq_len, lag, n_cat_var, n_num_var, n_embd, dropout, device) -> None:
        super().__init__()
        self.prec = prec
        self.device = device
        self.len = seq_len-lag
        #categorical
        self.n_grn_cat = n_cat_var
        self.GRNs_cat = nn.ModuleList([
            GRN(n_embd, dropout) for _ in range(self.n_grn_cat)
        ])
        tot_var = n_cat_var
        #numerical
        if prec:
            self.n_grn_num = n_num_var
            self.GRNs_num = nn.ModuleList([
                GRN(n_embd, dropout) for _ in range(self.n_grn_num)
            ])
            tot_var = tot_var+n_num_var
        #flatten
        emb_dims = [n_embd*tot_var, int((n_embd+tot_var)/2), tot_var]
        self.flatten_GRN = flatten_GRN(emb_dims, dropout)

    def forward(self, categorical, y=None):
        # import pdb
        # pdb.set_trace()
        var_sel = self.get_cat_GRN(categorical)
        to_be_flat = categorical
        if y is not None:
            assert self.prec==True
            num_after_GRN = self.get_num_GRN(y)
            var_sel = torch.cat((var_sel, num_after_GRN), dim = 2)
            to_be_flat = torch.cat((to_be_flat, y), dim=2)
        var_sel_wei = self.get_flat_GRN(to_be_flat)
        out = var_sel*var_sel_wei.unsqueeze(3)
        out = torch.sum(out, 2)/out.size(2)
        return out

    def get_cat_GRN(self, x):
        cat_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_cat):
            grn = layer(x[:,:,index,:])
            cat_after_GRN = torch.cat((cat_after_GRN, grn.unsqueeze(2)), dim=2)
        return cat_after_GRN
    
    def get_num_GRN(self, x):
        num_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_num):
            grn = layer(x[:,:,index,:])
            num_after_GRN = torch.cat((num_after_GRN, grn.unsqueeze(2)), dim=2)
        return num_after_GRN
    
    def get_flat_GRN(self, to_be_flat):
        # apply flatten_GRN and softmax
        emb = torch.flatten(to_be_flat, start_dim=2) # [bs, seq_len, num_var*n_embd]
        var_sel_wei = self.flatten_GRN(emb)
        return var_sel_wei
    
class Decoder_LSTM(nn.Module):
    def __init__(self, n_layers, n_embd, dropout, device) :
        super().__init__()
        self.device = device
        self.num_layers = n_layers
        self.hidden_size = n_embd
        self.LSTM = nn.LSTM(input_size=n_embd, hidden_size=self.hidden_size, num_layers=self.num_layers, batch_first = True)
        self.dropout = nn.Dropout(dropout)
        self.LSTM_enc_GLU = GLU(n_embd)
        self.norm = nn.LayerNorm(n_embd)

    def forward(self, x, h0, c0):
        '''
        After Variable Selection, its output goes through:\n
         - Lstm_Dec (h0 and c0 come from LSTM_Enc)\n
         - Dropout\n
         - GLU\n
         - Add (residual connection)\n
         - Norm (LayerNorm)
        '''
        # h0 = torch.zeros(self.num_layers, x.size(0), x.size(2)).to(self.device)
        # c0 = torch.zeros(self.num_layers, x.size(0), x.size(2)).to(self.device)
        lstm_dec, _ = self.LSTM(x, (h0,c0))
        lstm_dec = self.dropout(lstm_dec)
        output_dec = self.norm(self.LSTM_enc_GLU(lstm_dec) + x)
        return output_dec

class postTransformer(nn.Module):
    def __init__(self, n_embd, dropout) -> None:
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        self.GLU1 = GLU(n_embd)
        self.norm1 = nn.LayerNorm(n_embd)
        self.GRN = GRN(n_embd, dropout)
        self.GLU2 = GLU(n_embd)
        self.norm2 = nn.LayerNorm(n_embd)
    
    def forward(self, x, pre_transformer):
        # import pdb
        # pdb.set_trace()
        x = self.dropout(x)
        x = self.norm1(x + self.GLU1(x))
        x = self.GRN(x)
        out = self.norm2(pre_transformer + self.GLU2(x))
        return out
    
if __name__=='__main__':
    from dataloading import dataloading
    from sklearn.preprocessing import StandardScaler

    bs = 8
    bs_test = 4
    seq_len = 265
    lag = 65
    hour = 24
    hour_test = 24
    train = True
    step = 1
    scaler_type = StandardScaler()
    path_data = '/home/andrea/timeseries/data/edison/processed.pkl' 
    train_dl, _, _, _ = dataloading(batch_size=bs, batch_size_test=bs_test, 
                                                        seq_len=seq_len, lag=lag,
                                                        hour_learning=hour, 
                                                        hour_inference=hour_test, 
                                                        train_bool=train,
                                                        step = step,
                                                        scaler_y = scaler_type,
                                                        path=path_data)
    
    x, y = next(iter(train_dl))
    # x.shape = [8, 256, 6]
    # y.shape = [8, 256]
    
    # tft = True
    n_embd = 4
    n_enc = 2
    n_dec = 2
    head_size = 2
    num_heads = 2
    fw_exp = 3
    device = 'cpu'
    dropout = 0.1
    n_layers = 3

    categorical = x[:,:,1:]
    # start embedding
    emb_cat_var = embedding_cat_variables(seq_len, lag, n_embd, device)
    emb_y_var = embedding_target(n_embd)
    embed_x = emb_cat_var(categorical) #                        torch.Size([8, 256, 8, 4])
    embed_y = emb_y_var(y.unsqueeze(dim=2)).unsqueeze(dim=2) #                   torch.Size([8, 256, 4])

    _,_,n_cat_var,_ = embed_x.shape
    _,_,n_num_var,_ = embed_y.shape # some unsqueeze only due to only one num var
    tot_var = n_cat_var + n_num_var

    embed_x_past = embed_x[:,:-lag,:,:]
    embed_y_past = embed_y[:,:-lag,:,:]
    embed_x_fut = embed_x[:,-lag:,:,:]
    embed_y_fut = embed_y[:,-lag:,:,:]

    # # init NN
    from encoder import Encoder
    from decoder import Decoder
    mix = False
    var_sel_enc = Encoder_Var_Selection(mix, seq_len, lag, n_cat_var, n_num_var, n_embd, dropout, device)
    lstm_enc = Encoder_LSTM(n_layers, n_embd, dropout, device)
    grn_enc = GRN(n_embd=n_embd, dropout=dropout)
    encoder = Encoder(n_enc, n_embd, num_heads, head_size, fw_exp, dropout)

    prec = False
    var_sel_dec = Decoder_Var_Selection(prec, seq_len, lag, n_cat_var, n_num_var, n_embd, dropout, device)
    lstm_dec = Decoder_LSTM(n_layers, n_embd, dropout, device)
    grn_dec = GRN(n_embd=n_embd, dropout=dropout)
    decoder = Decoder(n_dec, n_embd, num_heads, head_size, fw_exp, lag, dropout)

    # computation
    # ENCODER MIXING x AND y (mix variable to handle the difference)
    # var_sel_past = var_sel_enc(embed_x_past, embed_y_past)
    # lstm_encs, hn, cn = lstm_enc(var_sel_past)
    # pre_enc = grn_enc(lstm_encs)
    # encoding = encoder(pre_enc, pre_enc, pre_enc)

    var_sel_past = var_sel_enc(embed_x_past)
    lstm_encs, hn, cn = lstm_enc(var_sel_past)
    pre_enc = grn_enc(lstm_encs)
    encoding = encoder(embed_y_past.squeeze(2), pre_enc, pre_enc)

    # DECODER USING ONLY x (prec variable to handle the difference)
    var_sel_fut = var_sel_dec(embed_x_fut)
    lstm_decs = lstm_dec(var_sel_fut, hn, cn)
    pre_dec = grn_dec(lstm_decs)
    decoding = decoder(pre_dec, encoding, encoding)

    post_transformer = postTransformer(n_embd, dropout)
    out = post_transformer(decoding, pre_dec)
    
    import pdb
    pdb.set_trace()
    quantile = True
    if quantile:
        quantiles = [0.1, 0.5, 0.9]
        loss = [0]*len(quantiles)
        target = torch.randint(-2, 2, (bs, lag))
        # last_linear in model.py
        out_linear = nn.Linear(n_embd, len(quantiles))
        out = out_linear(out)
        for i, q in enumerate(quantiles):
            q_loss = torch.max(q*(target - out[:,:,i]), (1-q)*(out[:,:,i] - target))
            loss[i] = loss[i] + q_loss
