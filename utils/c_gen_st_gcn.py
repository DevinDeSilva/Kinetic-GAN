import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable

from .utils_gc_gan.tgcn import ConvTemporalGraphical
from .utils_gc_gan.graph import Graph


class NoiseInjection(nn.Module):
    def __init__(self, channel):
        super().__init__()

        self.weight = nn.Parameter(torch.zeros(1, channel, 1, 1))

    def forward(self, image, noise):
        return image + self.weight * noise


class Generator(nn.Module):
    
    def __init__(self, in_channels, n_classes, edge_importance_weighting=True, **kwargs):
        super().__init__()

        # load graph
        self.graph = Graph()
        self.A = [torch.tensor(Al, dtype=torch.float32, requires_grad=False).cuda() for Al in self.graph.As]

        # build networks
        spatial_kernel_size  = [A.size(0) for A in self.A]
        temporal_kernel_size = [3 for i, _ in enumerate(self.A)]
        kernel_size          = (temporal_kernel_size, spatial_kernel_size)
        self.t_size = t_size = 64

        #kwargs0 = {k: v for k, v in kwargs.items() if k != 'dropout'}
        self.st_gcn_networks = nn.ModuleList((
            st_gcn(in_channels+n_classes, 512, kernel_size, 1, graph=self.graph, lvl=3, bn=False, residual=False, up_s=False, up_t=int(t_size/16), **kwargs),
            st_gcn(512, 256, kernel_size, 1, graph=self.graph, lvl=2, up_s=True, up_t=int(t_size/16), **kwargs),
            st_gcn(256, 128, kernel_size, 1, graph=self.graph, lvl=2, up_s=False, up_t=int(t_size/8), **kwargs),
            st_gcn(128, 64, kernel_size, 1, graph=self.graph, lvl=1, up_s=True, up_t=int(t_size/4), **kwargs),
            st_gcn(64, 32, kernel_size, 1, graph=self.graph, lvl=1, up_s=False, up_t=int(t_size/2), **kwargs),
            st_gcn(32, 3, kernel_size, 1, graph=self.graph, lvl=0, up_s=True, **kwargs),
            st_gcn(3, 3, kernel_size, 1, graph=self.graph, lvl=0, tan=True, **kwargs)
        ))

        # initialize parameters for edge importance weighting
        if edge_importance_weighting:
            self.edge_importance = nn.ParameterList([
                nn.Parameter(torch.ones(self.A[i.lvl].size()))
                for i in self.st_gcn_networks
            ])
        else:
            self.edge_importance = [1] * len(self.st_gcn_networks)

        self.label_emb = nn.Embedding(n_classes, n_classes)
        

    def forward(self, x, labels):

        c = self.label_emb(labels)
        c = c.view(c.size(0), c.size(1), 1, 1).repeat(1, 1, int(self.t_size/16), 1)

        x = torch.cat((c, x), 1)
    
        # forward
        for gcn, importance in zip(self.st_gcn_networks, self.edge_importance):
            x, _ = gcn(x, self.A[gcn.lvl] * importance)

        return x

    

class st_gcn(nn.Module):

    def __init__(self,
                in_channels,
                out_channels,
                kernel_size,
                stride=1,
                graph=None,
                lvl=3,
                dropout=0,
                bn=True,
                residual=True,
                up_s=False, 
                up_t=64, 
                tan=False):
        super().__init__()

        assert len(kernel_size) == 2
        assert kernel_size[0][lvl] % 2 == 1
        padding = ((kernel_size[0][lvl] - 1) // 2, 0)
        self.graph, self.lvl, self.up_s, self.up_t, self.tan = graph, lvl, up_s, up_t, tan
        self.gcn = ConvTemporalGraphical(in_channels, out_channels,
                                        kernel_size[1][lvl])

        tcn = [nn.Conv2d(
                out_channels,
                out_channels,
                (kernel_size[0][lvl], 1),
                (stride, 1),
                padding,
            )]
        
        tcn.append(nn.BatchNorm2d(out_channels)) if bn else None

        self.tcn = nn.Sequential(*tcn)


        if not residual:
            self.residual = lambda x: 0

        elif (in_channels == out_channels) and (stride == 1):
            self.residual = lambda x: x

        else:
            self.residual = nn.Sequential(
                nn.Conv2d(
                    in_channels,
                    out_channels,
                    kernel_size=1,
                    stride=(stride, 1)),
                nn.BatchNorm2d(out_channels),
            )

        self.noise = NoiseInjection(out_channels)

        self.l_relu = nn.LeakyReLU(0.2, inplace=True)
        self.tanh   = nn.Tanh()

    def forward(self, x, A):

        x = self.upsample_s(x) if self.up_s else x
        
        x = F.interpolate(x, size=(self.up_t,x.size(-1)))  # Exactly like nn.Upsample

        res = self.residual(x)
        x, A = self.gcn(x, A)
        x    = self.tcn(x) + res

        # Noise Inject
        noise = torch.randn(x.size(0), 1, x.size(2), x.size(3), device='cuda:0')
        x     = self.noise(x, noise)

        return self.tanh(x) if self.tan else self.l_relu(x), A

    
    def upsample_s(self, tensor):

        ids  = []
        mean = []
        for umap in self.graph.mapping[self.lvl]:
            ids.append(umap[0])
            tmp = None
            for nmap in umap[1:]:
                tmp = torch.unsqueeze(tensor[:, :, :, nmap], -1) if tmp == None else torch.cat([tmp, torch.unsqueeze(tensor[:, :, :, nmap], -1)], -1)

            mean.append(torch.unsqueeze(torch.mean(tmp, -1) / (2 if self.lvl==2 else 1), -1))

        for i, idx in enumerate(ids): tensor = torch.cat([tensor[:,:,:,:idx], mean[i], tensor[:,:,:,idx:]], -1)


        return tensor