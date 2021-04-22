import numpy as np
import networkx as nx

class Graph():
    """ The Graph to model the skeletons extracted by the openpose

    Args:
        strategy (string): must be one of the follow candidates
        - uniform: Uniform Labeling
        - distance: Distance Partitioning
        - spatial: Spatial Configuration
        For more information, please refer to the section 'Partition Strategies'
            in our paper (https://arxiv.org/abs/1801.07455).

        layout (string): must be one of the follow candidates
        - openpose: Is consists of 18 joints. For more information, please
            refer to https://github.com/CMU-Perceptual-Computing-Lab/openpose#output
        - ntu-rgb+d: Is consists of 25 joints. For more information, please
            refer to https://github.com/shahroudy/NTURGB-D

        max_hop (int): the maximal distance between two connected nodes
        dilation (int): controls the spacing between the kernel points

    """

    def __init__(self,
                 max_hop=1,
                 dilation=1):
        self.max_hop  = max_hop
        self.dilation = dilation
        self.lvls     = 4  # 25 -> 11 -> 5 -> 1
        self.As       = []
        self.hop_dis  = []

        self.get_edge()
        for lvl in range(self.lvls):
            self.hop_dis.append(get_hop_distance(self.num_node, self.edge, lvl, max_hop=max_hop))
            self.get_adjacency(lvl)

    def __str__(self):
        return self.As

    def get_edge(self):
        self.num_node = []
        self.nodes = []
        self.center = [21 - 1]
        self.nodes = []
        self.Gs = []
        
        neighbor_base = [(1, 2), (2, 21), (3, 21), (4, 3), (5, 21),
                        (6, 5), (7, 6), (8, 7), (9, 21), (10, 9),
                        (11, 10), (12, 11), (1, 13), (14, 13), (15, 14),
                        (16, 15), (1, 17), (18, 17), (19, 18), (20, 19),
                        (22, 8), (23, 8), (24, 12), (25, 12)]
        neighbor_link = [(i - 1, j - 1) for (i, j) in neighbor_base]

        nodes = [i for i in range(25)]
        G = nx.Graph()
        G.add_nodes_from(nodes)
        G.add_edges_from(neighbor_link)
        G = nx.convert_node_labels_to_integers(G, first_label=0)

        self_link = [(int(i), int(i)) for i in G]

        self.edge = [np.concatenate((np.array(G.edges), self_link), axis=0)]
        self.nodes.append(nodes)
        self.num_node.append(len(G))
        self.Gs.append(G.copy())


        for _ in range(self.lvls-1):
            stay  = []
            start = 1
            while True:
                remove = []
                for i in G:
                    if len(G.edges(i)) == start and i not in stay:
                        lost = []
                        for j,k in G.edges(i):
                            stay.append(k)
                            lost.append(k)
                        recon = [(l,m) for l in lost for m in lost if l!=m]
                        G.add_edges_from(recon)            
                        remove.append(i)

                if start>10: break  # Remove as maximum as possible
                G.remove_nodes_from(remove)

                cycle = nx.cycle_basis(G)  # Check if there is a cycle in order to downsample it
                if len(cycle)>0:
                    if len(cycle[0])==len(G):
                        last = [x for x in G if x not in stay]
                        G.remove_nodes_from(last)

                start+=1

            mapping = {}
            for i, x in enumerate(G): 
                mapping[int(x)] = i
                if int(x)==self.center[-1]:
                    self.center.append(i)
            G = nx.relabel_nodes(G, mapping)
            G = nx.convert_node_labels_to_integers(G, first_label=0)
            
            nodes = [i for i in range(len(G))]
            self.nodes.append(nodes)

            self_link = [(int(i), int(i)) for i in G]
            G_l = np.concatenate((np.array(G.edges), self_link), axis=0) if len(np.array(G.edges)) > 0 else self_link
            self.edge.append(G_l)
            self.num_node.append(len(G))
            self.Gs.append(G.copy())
            

        assert len(self.num_node) == self.lvls
        assert len(self.nodes)    == self.lvls
        assert len(self.edge)     == self.lvls
        assert len(self.center)   == self.lvls
        
        
    def get_adjacency(self, lvl):
        valid_hop = range(0, self.max_hop + 1, self.dilation)
        adjacency = np.zeros((self.num_node[lvl], self.num_node[lvl]))
        for hop in valid_hop:
            adjacency[self.hop_dis[lvl] == hop] = 1
        normalize_adjacency = normalize_digraph(adjacency)

        A = []
        for hop in valid_hop:
            a_root = np.zeros((self.num_node[lvl], self.num_node[lvl]))
            a_close = np.zeros((self.num_node[lvl], self.num_node[lvl]))
            a_further = np.zeros((self.num_node[lvl], self.num_node[lvl]))
            for i in range(self.num_node[lvl]):
                for j in range(self.num_node[lvl]):
                    if self.hop_dis[lvl][j, i] == hop:
                        if self.hop_dis[lvl][j, self.center[lvl]] == self.hop_dis[lvl][i, self.center[lvl]]:
                            a_root[j, i] = normalize_adjacency[j, i]
                        elif self.hop_dis[lvl][j, self.center[lvl]] > self.hop_dis[lvl][i, self.center[lvl]]:
                            a_close[j, i] = normalize_adjacency[j, i]
                        else:
                            a_further[j, i] = normalize_adjacency[j, i]
            if hop == 0:
                A.append(a_root)
            else:
                A.append(a_root + a_close)
                A.append(a_further)
        A = np.stack(A)
        self.As.append(A)
            


def get_hop_distance(num_node, edge, lvl, max_hop=1):
    A = np.zeros((num_node[lvl], num_node[lvl]))
    for i, j in edge[lvl]:
        A[j, i] = 1
        A[i, j] = 1

    # compute hop steps
    hop_dis = np.zeros((num_node[lvl], num_node[lvl])) + np.inf
    transfer_mat = [np.linalg.matrix_power(A, d) for d in range(max_hop + 1)]
    arrive_mat = (np.stack(transfer_mat) > 0)
    for d in range(max_hop, -1, -1):
        hop_dis[arrive_mat[d]] = d
    return hop_dis


def normalize_digraph(A):
    Dl = np.sum(A, 0)
    num_node = A.shape[0]
    Dn = np.zeros((num_node, num_node))
    for i in range(num_node):
        if Dl[i] > 0:
            Dn[i, i] = Dl[i]**(-1)
    AD = np.dot(A, Dn)
    return AD


def normalize_undigraph(A):
    Dl = np.sum(A, 0)
    num_node = A.shape[0]
    Dn = np.zeros((num_node, num_node))
    for i in range(num_node):
        if Dl[i] > 0:
            Dn[i, i] = Dl[i]**(-0.5)
    DAD = np.dot(np.dot(Dn, A), Dn)
    return DAD