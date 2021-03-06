import logging
import argparse
import ipdb
import random
from datetime import datetime
from itertools import product
from tqdm import tqdm
import numpy as np
import pickle
from IPython import embed
from ipdb import slaunch_ipdb_on_exception

from synpo.agent import *
from synpo.component import *
from synpo.utils import *
import synpo.gridworld as gridworld

from synpo.utils import mkdir, set_seed

parser = argparse.ArgumentParser()

parser.add_argument('--extend_mode', action='store_true',
                            help="train on the first (10 ENV, 10 TASK) combinations.")

parser.add_argument('--network', default='synpo', choices=['mlp', 'mtl', 'synpo'],
                            help="select model architecture")

parser.add_argument('--batch_size', default=128, type=int)

parser.add_argument('--embedding_dim', default=128, type=int)
parser.add_argument('--scene_embedding_dim', default=128, type=int)
parser.add_argument('--task_embedding_dim', default=128, type=int)

parser.add_argument('--scene_num', default=5, type=int)
parser.add_argument('--task_num', default=5, type=int)

parser.add_argument('--lr', default=0.001, type=float,
                            help="base learning rate")

parser.add_argument('--evaluate', action='store_true',
                            help="evaluation mode")

parser.add_argument('--logger_name', default='log/synpo_{}_{}_{}_{}.log', type=str,
                            help="logger name format [must have for slots to fill]")
parser.add_argument('--norm', action='store_true',
                            help="whether normalize the scene/task embedding")

parser.add_argument('--weight', default=None, type=str)

parser.add_argument('--repeat', default=10, type=int,
                            help="number of test run")

parser.add_argument('--gpu_id', default=0, type=int)
parser.add_argument('--scene', default=None, type=int)
parser.add_argument('--task',    default=None, type=int)

parser.add_argument('--num_obj_types', default=5, type=int)
parser.add_argument('--task_length',     default=2, type=int)
parser.add_argument('--update_interval', default=1, type=int)

parser.add_argument('--reward_prediction', default=1, type=int,
                            help="loss weight of reward prediction objective")
parser.add_argument('--scene_disentanglement', default=0.1, type=float, 
                            help="loss weight of scene disentanglement prediction objective")
parser.add_argument('--task_disentanglement', default=0.1, type=float,
                            help="loss weight of task disentanglement prediction objective")

parser.add_argument('--wd', action='store_true', 
                            help="enable weight decay")
parser.add_argument('--mode', default='cloning', choices=['cloning'],
                            help="training mode [only behavior cloing available for now]")
parser.add_argument('--postfix', default='', type=str,
                            help="postfix to the log file")


parser.add_argument('--visualize', action='store_true',
                            help="visualize policy [only in evaluation mode]")
parser.add_argument('--random_seed', default=0, type=int,
                            help="random seed value")

parser.add_argument('--split_filepath', default=None, type=str,
                            help="train/test split filepath")

args = parser.parse_args()

def get_network(task):
    arg_dim = task.env.observation_space.spaces[1].shape[0]
    grid_dim = task.env.observation_space.spaces[0].shape[0]
    action_dim = task.env.action_space.n
    if args.network == 'mlp':
        network = GridWorldMLP(grid_dim, action_dim, arg_dim, 
                                scene_num=args.scene_num,
                                task_num=args.task_num,
                                embed_dim=args.embedding_dim, 
                                scene_dim=args.scene_embedding_dim, 
                                task_dim=args.task_embedding_dim,
                                gpu=args.gpu_id, 
                                scene_disentanglement=args.scene_disentanglement, 
                                task_disentanglement=args.task_disentanglement,
                                norm=args.norm)
    elif args.network == 'mtl':
        network = GridWorldMTL(grid_dim, action_dim, arg_dim, 
                                scene_num=args.scene_num,
                                task_num=args.task_num,
                                embed_dim=args.embedding_dim,
                                scene_dim=args.scene_embedding_dim, 
                                task_dim=args.task_embedding_dim,
                                gpu=args.gpu_id, 
                                scene_disentanglement=args.scene_disentanglement, 
                                task_disentanglement=args.task_disentanglement,
                                norm=args.norm)
    elif args.network == 'synpo':
        network = GridWorldSynPo(grid_dim, action_dim, arg_dim, 
                                scene_num=args.scene_num,
                                task_num=args.task_num,
                                embed_dim=args.embedding_dim,
                                scene_dim=args.scene_embedding_dim,
                                task_dim=args.task_embedding_dim,
                                gpu=args.gpu_id,
                                norm=args.norm)
    else:
        raise ValueError('Non-supported Network')
    return network

def gridworld_behaviour_cloning(args, layouts, train_combos, test_combos):

    config = Config()

    grid_world_task = GridWorldTask(layouts,
                                    num_obj_types=args.num_obj_types,
                                    task_length=args.task_length,
                                    history_length= config.history_length,
                                    train_combos=train_combos,
                                    test_combos=test_combos)

    config.task_fn = lambda: grid_world_task

    if args.wd: 
        print('with weight decay!')
        config.optimizer_fn = lambda params: torch.optim.Adam(params, lr=args.lr, weight_decay=10e-5)
    else:
        print('without weight decay!')
        config.optimizer_fn = lambda params: torch.optim.Adam(params, lr=args.lr)

    network = get_network(grid_world_task)

    if args.weight is not None: 
        network.load_state_dict(torch.load(args.weight)['best_model_weight'])
        
    print(network)

    config.network_fn = lambda: network

    config.replay_fn = lambda: TrajectoryReplay(memory_size=20000, max_length=200, batch_size=64) # number of trajectory per batch

    config.policy_fn = lambda: GreedyPolicy(epsilon=0.1, final_step=500000, min_epsilon=0.0)

    config.logger = Logger('./log', logger)

    config.test_interval = 2000

    config.exploration_steps = 50000

    config.postfix = args.postfix

    config.tag = network.__class__.__name__

    config.update_interval = 1 # preset

    config.scene_disentanglement_coeff = args.scene_disentanglement

    config.task_disentanglement_coeff = args.task_disentanglement

    config.max_eps = args.scene_num * args.task_num * 2000

    return GridBehaviourCloning(config)

if __name__ == '__main__':

    mkdir('data')
    mkdir('log')
    os.system('export OMP_NUM_THREADS=1')

    if args.extend_mode: # Hardcoding numbers of scenes and tasks for training
        args.scene_num = 2
        args.task_num  = 2

    set_seed(args.random_seed, c=args.random_seed)

    # with open(args.split_filepath, 'rb') as handle:
    #   data = pickle.load(handle)
    # args.task_num  = data['task_num']
    # args.scene_num = data['scene_num']
    # train_combos   = data['train_combos']
    # test_combos    = data['test_combos']
    # layouts        = data['layouts']

    layouts = ['map{}'.format(i+1) for i in range(0, 2) ]
    train_combos = [(i, j) for i, j in product(range(args.scene_num), range(args.task_num))]
    test_combos  = [(i, j) for i, j in product(range(args.scene_num), range(args.task_num))]
    print('num train:', len(train_combos), 'num test:', len(test_combos))  

    # ipdb.set_trace()

    if args.mode == 'cloning':
        print('Loading Episodic Behavior Cloning')
        agent = gridworld_behaviour_cloning(args, layouts, train_combos, test_combos)

    agent.reward_prediction = args.reward_prediction

    agent.split_name = 'quad'  

    if args.evaluate:

        # print('Testing on test...')
        # avg_test_reward, avg_test_success_rate = test_agent(agent)
        # print('Avg test success rate %f, Avg test reward %f' % (avg_test_success_rate, avg_test_reward))

        with slaunch_ipdb_on_exception():
            traj_length = []
            if args.scene is not None or args.task is not None:
                if args.scene is not None and args.task is None:
                    index_scene = args.scene
                    index_task = random.sample([x[1] for x in train_combos if x[0] == args.scene], 1)[0]
                else:
                    index_scene = args.scene if args.scene is not None else np.random.randint(args.scene_num)
                    index_task = args.task if args.task is not None else np.random.randint(args.task_num)
                for _ in tqdm(range(args.repeat)):
                    success, traj_len, _, _, _, _ = agent.evaluate(visualize=args.visualize, 
                                                             index=(index_scene, index_task)) # main program
                    if success: 
                        traj_length.append(traj_len)
                print('mean length:', np.mean(traj_length))
            else:
                rates = []
                for combo in train_combos:
                    success_list = []
                    trajectory_list = []
                    for _ in tqdm(range(args.repeat)):
                        success, traj_len, _, _, _, _ = agent.evaluate(visualize=args.visualize, index=combo) # main program
                        success_list.append(success)
                        trajectory_list.append(traj_len)
                    success_rate = sum(success_list) / len(success_list)
                    rates.append(success_rate)
                    print('* [Task={}, # of Tests={}] Average success rate: {:.4f}, Average trajectory length: {}'.format( combo, args.repeat,
                                                                    success_rate, sum(trajectory_list) / len(trajectory_list) ))
                print('average success rate: {:.4f}'.format(np.mean(rates)))

    else:
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(args.logger_name.format(agent.__class__.__name__,
                                                            agent.learning_network.__class__.__name__,
                                                            datetime.now().strftime("%Y-%m-%d_%H:%M:%S"),
                                                            args.postfix))
        logger.addHandler(handler)

        with slaunch_ipdb_on_exception():
            train_agent(agent) # main program

