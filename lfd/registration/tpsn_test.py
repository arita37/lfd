import scipy.io
import numpy as np
import scipy.spatial.distance as ssd
import IPython as ipy
import hc
import settings 
import matplotlib
import matplotlib.pyplot as plt
from lfd.rapprentice import plotting_plt
import tps_experimental
import tps
from tps_experimental import ThinPlateSpline, ThinPlateSplineNormal
import h5py
import lfd.rapprentice.math_utils as mu

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("index", type=int, nargs='?', default=3, help="index of problem to generate")
parser.add_argument("--outfile", '-o', type=str, default=None)
args = parser.parse_args()

class Registration(object):
    def __init__(self, demo, test_scene_state, f, corr):
        self.demo = demo
        self.test_scene_state = test_scene_state
        self.f = f
        self.corr = corr
    
    def get_objective(self):
        raise NotImplementedError

class TpsRpmRegistration(Registration):
    def __init__(self, demo, test_scene_state, f, corr, rad):
        super(TpsRpmRegistration, self).__init__(demo, test_scene_state, f, corr)
        self.rad = rad
    
    def get_objective(self):
        x_nd = self.demo.scene_state.cloud[:,:3]
        y_md = self.test_scene_state.cloud[:,:3]
        cost = self.get_objective2(x_nd, y_md, self.f, self.corr, self.rad)
        return cost
    
    @staticmethod
    def get_objective2(x_nd, y_md, f, corr_nm, rad):
        r"""Returns the following 5 objectives:
        
            - :math:`\frac{1}{n} \sum_{i=1}^n \sum_{j=1}^m m_{ij} ||y_j - f(x_i)||_2^2`
            - :math:`\lambda Tr(A^\top K A)`
            - :math:`Tr((B - I) R (B - I))`
            - :math:`\frac{2T}{n} \sum_{i=1}^n \sum_{j=1}^m m_{ij} \log m_{ij}`
            - :math:`-\frac{2T}{n} \sum_{i=1}^n \sum_{j=1}^m m_{ij}`
        """
        cost = np.zeros(5)
        xwarped_nd = f.transform_points(x_nd)
        dist_nm = ssd.cdist(xwarped_nd, y_md, 'sqeuclidean')
        n = len(x_nd)
        cost[0] = (corr_nm * dist_nm).sum() / n
        cost[1:3] = f.get_objective()[1:]
        corr_nm = np.reshape(corr_nm, (1,-1))
        nz_corr_nm = corr_nm[corr_nm != 0]
        cost[3] = (2*rad / n) * (nz_corr_nm * np.log(nz_corr_nm)).sum()
        cost[4] = -(2*rad / n) * nz_corr_nm.sum()
        return cost

class TpsnRpmRegistration(Registration):
    def __init__(self, demo, test_scene_state, f, corr, x_ld, u_rd, z_rd, y_md, v_sd, z_sd, rad, radn, bend_coef, rot_coef):
        super(TpsRpmRegistration, self).__init__(demo, test_scene_state, f, corr)
        self.x_ld = x_ld
        self.u_rd = u_rd
        self.z_rd = z_rd
        self.y_md = y_md
        self.v_sd = v_sd
        self.z_sd = z_sd
        self.rad = rad
        self.radn = radn
        self.bend_coef = bend_coef
        self.rot_coef = rot_coef 
    
    def get_objective(self):
        x_nd = self.demo.scene_state.cloud[:,:3]
        y_md = self.test_scene_state.cloud[:,:3]
        # TODO: fill x_ld, u_rd, z_rd, y_md, v_sd, z_sd
        cost = self.get_objective2(x_ld, u_rd, z_rd, y_md, v_sd, z_sd, self.f, self.corr_lm, self.corr_rs, self.rad, self.radn, self.bend_coef, self.rot_coef)
        return cost

    @staticmethod
    def get_objective2(x_ld, u_rd, z_rd, y_md, v_sd, z_sd, f, corr_lm, corr_rs, rad, radn, bend_coef, rot_coef):
        r"""Returns the following 5 objectives:
        
            - :math:`\frac{1}{n} \sum_{i=1}^n \sum_{j=1}^m m_{ij} ||y_j - f(x_i)||_2^2`
            - :math:`\lambda Tr(A^\top K A)`
            - :math:`Tr((B - I) R (B - I))`
            - :math:`\frac{2T}{n} \sum_{i=1}^n \sum_{j=1}^m m_{ij} \log m_{ij}`
            - :math:`-\frac{2T}{n} \sum_{i=1}^n \sum_{j=1}^m m_{ij}`
        """
        cost = np.zeros(8)
        xwarped_ld = f.transform_points()
        uwarped_rd = f.transform_vectors()
        zwarped_rd = f.transform_points(z_rd)

        beta_r = np.linalg.norm(uwarped_rd, axis=1)

        dist_lm = ssd.cdist(xwarped_ld, y_md, 'sqeuclidean')
        dist_rs = ssd.cdist(uwarped_rd / beta_r[:,None], v_sd, 'sqeuclidean')
        site_dist_rs = ssd.cdist(zwarped_rd, z_sd, 'sqeuclidean')
        prior_prob_rs = np.exp( -site_dist_rs / (2*rad) )

        l = len(x_ld)
        r = len(u_rd)
        # point matching cost
        cost[0] = (corr_lm * dist_lm).sum() / l
        # normal matching cost
        cost[1] = (corr_rs * dist_rs).sum() / r

        # bending cost
        cost[2] = f.compute_bending_energy(bend_coef=bend_coef)

        # rotation cost
        cost[3] = f.compute_rotation_reg(rot_coef=rot_coef)

        # point entropy
        corr_lm = np.reshape(corr_lm, (1,-1))
        nz_corr_lm = corr_lm[corr_lm != 0]
        cost[4] = (2*rad / l) * (nz_corr_lm * np.log(nz_corr_lm)).sum()
        cost[5] = -(2*rad / l) * nz_corr_lm.sum()

        # normal entropy
        corr_rs = np.reshape(corr_rs, (1,-1))
        nz_corr_rs = corr_rs[corr_rs != 0]
        site_dist_rs = np.reshape(site_dist_rs, (1,-1))
        nz_site_dist_rs = site_dist_rs[corr_rs != 0]
        cost[6] = (2*radn / r) * (nz_corr_rs * np.log(nz_corr_rs / nz_site_dist_rs)).sum()
        cost[7] = -(2*radn / r) * nz_corr_rs.sum()
        return cost

def plot_warped_grid_2d(f, mins, maxes, grid_res=None, color='gray', flipax=False, draw=True):
    xmin, ymin = mins
    xmax, ymax = maxes
    ncoarse = 10
    nfine = 100

    if grid_res is None:
        xcoarse = np.linspace(xmin, xmax, ncoarse)
        ycoarse = np.linspace(ymin, ymax, ncoarse)
    else:
        xcoarse = np.arange(xmin, xmax, grid_res)
        ycoarse = np.arange(ymin, ymax, grid_res)
    xfine = np.linspace(xmin, xmax, nfine)
    yfine = np.linspace(ymin, ymax, nfine)

    lines = []

    sgn = -1 if flipax else 1

    for x in xcoarse:
        xy = np.zeros((nfine, 2))
        xy[:,0] = x
        xy[:,1] = yfine
        lines.append(f(xy)[:,::sgn])

    for y in ycoarse:
        xy = np.zeros((nfine, 2))
        xy[:,0] = xfine
        xy[:,1] = y
        lines.append(f(xy)[:,::sgn])        

    lc = matplotlib.collections.LineCollection(lines,colors=color,lw=.5)
    ax = plt.gca()
    ax.add_collection(lc)
    if draw:
        plt.draw()

def generate_path(way_points, max_step=1.5, y_sym=True):
    way_points = np.asarray(way_points)
    if y_sym:
        way_points = np.r_[way_points, np.r_[-1,1][None,:] * way_points[::-1]]
    path = []
    for pt0, pt1 in zip(way_points[range(-1,len(way_points))], way_points):
        path.append(mu.linspace2d(pt0, pt1, np.ceil(1 + np.linalg.norm(pt1 - pt0)/max_step))[1:])
    wp_inds = np.cumsum([len(seg) for seg in path]) - 1
    path = np.concatenate(path, axis=0)
    return path, wp_inds

def generate_normal_path(path):
    r = len(path)
    z_rd = (path[range(-1, r-1)] + path)/2
    u_rd = (path - path[range(-1, r-1)])
    u_rd = np.c_[u_rd[:,1], -u_rd[:,0]]
    u_rd /= np.linalg.norm(u_rd, axis=1)[:,None]
    return u_rd, z_rd

def generate_problem(index):
    # rad_init, reg_init, radn_init, radn_final, nu_init, nu_final
    x_wp_inds, y_wp_inds = None, None
    rad_final, reg_final = None, None
    if index == 0:
        # semicircles and triangles
        x_ld = np.c_[np.linspace(-4, 4, 21), np.r_[np.linspace(0, 2, 6), np.linspace(2, -2, 11)[1:], np.linspace(-2, 0, 6)[1:]]]
        u_rd = np.array([[-1, 1]]*4 + [[0, 1]] + [[1, 1]]*7 + [[0, 1]] + [[-1, 1]]*4)
        u_rd = u_rd / np.linalg.norm(u_rd, axis=1)[:, None]
        z_rd = np.c_[np.linspace(-4, 4, 17), np.r_[np.linspace(0, 2, 5), np.linspace(2, -2, 9)[1:], np.linspace(-2, 0, 5)[1:]]]

        angles = np.linspace(-np.pi/2, np.pi/2, 15)
        semi_circle_points = np.c_[np.sin(angles), np.cos(angles)]
        y_md = np.r_[np.array([-2,0]) + 2 * semi_circle_points, np.array([2,0]) + 2 * np.array([1,-1]) * semi_circle_points[1:]]
        angles = np.linspace(-np.pi/2, np.pi/2, 11)
        semi_circle_points = np.c_[np.sin(angles), np.cos(angles)]
        v_sd = np.r_[semi_circle_points, np.array([-1,1]) * semi_circle_points[1:]]
        z_sd = np.r_[np.array([-2,0]) + 2 * semi_circle_points, np.array([2,0]) + 2 * np.array([1,-1]) * semi_circle_points[1:]]
    elif index == 1:
        # milk box
        x_ld, x_wp_inds = generate_path([[5, 0], [5, 8], [1, 8], [1, 11]])
        u_rd, z_rd = generate_normal_path(x_ld)
        y_md, y_wp_inds = generate_path([[5, 0], [5, 5], [1, 5], [1, 11]])
        v_sd, z_sd = generate_normal_path(y_md)

        # rad_init = 1
        # rad_final = .01
        # reg_init = 10
        # reg_final = .001
        # radn_init=1, radn_final=.05, nu_init=.1
    elif index == 2:
        # vase
        x_ld, x_wp_inds = generate_path([[2, 0], [4, 4], [1, 8], [3, 10]])
        # x_ld[:,0] += 2
        # ang = np.pi/16
        # R_dd = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
        # x_ld = x_ld.dot(R_dd.T)
        u_rd, z_rd = generate_normal_path(x_ld)
        y_md, y_wp_inds = generate_path([[4, 0], [4, 5], [1, 9], [2, 12]])
        v_sd, z_sd = generate_normal_path(y_md)
    elif index == 3:
        # deformed vase
        x_ld, x_wp_inds = generate_path([[2.5, 0], [4, 4], [1, 8], [3, 10]], max_step=1.0)
        # x_ld[:,0] += 2
        # ang = np.pi/16
        # R_dd = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
        # x_ld = x_ld.dot(R_dd.T)
        u_rd, z_rd = generate_normal_path(x_ld)
        y_md, y_wp_inds = generate_path([[2.75, 0], [5, 4], [4, 7], [6, 8], [-1, 10], [1, 8], [-4, 5], [-2.25, 0]], max_step=1.0, y_sym=False)
        # y_md[:,1] -= 1
        v_sd, z_sd = generate_normal_path(y_md)

        rad_final = .01
        reg_final = .01
    else:
        angles = np.linspace(-np.pi, np.pi, 12)
        circle_points = np.c_[np.sin(angles), np.cos(angles)]
        angles_off = (angles[range(-1, 12-1)] + angles)/2
        circle_points_off = np.c_[np.sin(angles_off), np.cos(angles_off)]
        square_points, corner_inds = generate_path([[2, -2], [2, 2]])
        square_normals_off, square_points_off = generate_normal_path(square_points)

        x_ld = np.r_[np.array([4,0]) + 2 * circle_points, 
                     np.array([-4,0]) + square_points]
        u_rd = np.r_[circle_points_off, square_normals_off]
        z_rd = np.r_[np.array([4,0]) + 2 * circle_points_off, 
                     np.array([-4,0]) + square_points_off]
        x_wp_inds = len(circle_points) + corner_inds

        y_md = np.r_[np.array([-1,6]) + 2 * circle_points, 
                     np.array([-4,0]) + square_points]
        v_sd = np.r_[circle_points_off, square_normals_off]
        z_sd = np.r_[np.array([-1,6]) + 2 * circle_points_off, 
                     np.array([-4,0]) + square_points_off]
        y_wp_inds = len(circle_points) + corner_inds

        rad_final = .01
        reg_final = .06

    return x_ld, u_rd, z_rd, y_md, v_sd, z_sd, x_wp_inds, y_wp_inds, rad_final, reg_final

def get_params(index):
    tpsn_min_param, tps_min_param = None, None
    tpsn_min_param_ranges, tps_min_param_ranges = None, None
    if index == 0:
        pass
    elif index == 1:
        pass
    elif index == 2:
        pass
    elif index == 3:
        # rad_init, reg_init, radn_init, radn_final, nu_init, nu_final = tpsn_min_param
        # not final
        # tpsn_min_param = (10, 10, 0.5, 0.1, 0.01, 1)
        # tps_min_param = (1, 1)
        # tpsn_min_param = (0.1, 0.1, 0.5, 0.1, 0.1, 1)
        # tps_min_param = (1, 10)
        # tpsn_min_param = (10, 10, 0.5, 0.1, 0.1, 1)
        tpsn_min_param = (0.1, 10, 0.005, 0.001, 0.1, 10)
        tps_min_param = (1, 10)
        # tpsn_min_param = (0.0625, 16.0, 0.0009765625, 0.000244140625, 0.03125, 2.0)
        # tpsn_min_param = (1.0, 16.0)

        # tpsn_min_param_ranges = []
        # tps_min_param_ranges = []
        # for param in tpsn_min_param:
        #     tpsn_min_param_ranges.append(np.exp2(np.arange(round(np.log2(param))-2, round(np.log2(param))+3)))
        # for param in tps_min_param:
        #     tps_min_param_ranges.append(np.exp2(np.arange(round(np.log2(param))-2, round(np.log2(param))+3)))
        # tpsn_min_param = None
        # tps_min_param = None
        pass
    else:
        # tpsn_min_param = (10, 1, 0.005, 0.001, 0.1, 1)
        tpsn_min_param = (10, 10, 0.005, 0.001, 0.1, 1)
        tps_min_param = (10, 1)
        pass
    return tpsn_min_param, tps_min_param, tpsn_min_param_ranges, tps_min_param_ranges

def plot_vectors(u_rd, z_rd, *args, **kwargs):
    r = u_rd.shape[0]
    lines = np.c_[z_rd, z_rd + u_rd, np.array([[None, None]]*r)].reshape(-1,2)
    plt.plot(lines[:,0], lines[:,1], *args, **kwargs)

# plt.ion()
# fig = plt.figure("fig")
# fig.clear()
# plt.axis('equal')
# plt.plot(x_ld[:,0], x_ld[:,1], "+")
# plot_vectors(u_rd, z_rd)
# plt.plot(y_md[:,0], y_md[:,1], "x")
# plot_vectors(v_sd, y_md)

def callback(i, i_em, x_ld, y_md, xtarg_ld, utarg_rd, wt_n, f, corr_lm, rad):
    plt.ion()
    fig = plt.figure("fig")
    fig.clear()
    plt.axis('equal')
    plt.plot(x_ld[:,0], x_ld[:,1], "r+")
    plot_vectors(u_rd, z_rd, "r")
    plt.plot(y_md[:,0], y_md[:,1], "bx")
    plot_vectors(v_sd, z_sd, "b")

    xwarped_ld = f.transform_points()
    uwarped_rd = f.transform_vectors()
    zwarped_rd = f.transform_points(z_rd)
    plt.plot(xwarped_ld[x_wp_inds,0], xwarped_ld[x_wp_inds,1], "go")
    plot_vectors(uwarped_rd, zwarped_rd, "g")
    plt.plot(xwarped_ld[x_wp_inds,0], xwarped_ld[x_wp_inds,1], "ro")

    plt.plot(xtarg_ld[:,0], xtarg_ld[:,1], "c+")
    plot_vectors(utarg_rd, zwarped_rd, "c")

    grid_means = .5 * (x_ld.max(axis=0) + x_ld.min(axis=0))
    grid_mins = grid_means - (x_ld.max(axis=0) - x_ld.min(axis=0))
    grid_maxs = grid_means + (x_ld.max(axis=0) - x_ld.min(axis=0))
    plotting_plt.plot_warped_grid_2d(f.transform_points, grid_mins, grid_maxs, draw=False)
    plt.draw()

def callback2(i, i_em, x_ld, y_md, xtarg_ld, wt_n, f, corr_lm, rad):
    plt.ion()
    fig = plt.figure("fig2")
    fig.clear()
    plt.axis('equal')
    plt.plot(x_ld[:,0], x_ld[:,1], "r+")
    plt.plot(y_md[:,0], y_md[:,1], "bx")

    xwarped_ld = f.transform_points(x_ld)
    plt.plot(xwarped_ld[x_wp_inds,0], xwarped_ld[x_wp_inds,1], "go")
    plt.plot(xwarped_ld[x_wp_inds,0], xwarped_ld[x_wp_inds,1], "ro")

    plt.plot(xtarg_ld[:,0], xtarg_ld[:,1], "c+")

    grid_means = .5 * (x_ld.max(axis=0) + x_ld.min(axis=0))
    grid_mins = grid_means - (x_ld.max(axis=0) - x_ld.min(axis=0))
    grid_maxs = grid_means + (x_ld.max(axis=0) - x_ld.min(axis=0))
    plotting_plt.plot_warped_grid_2d(f.transform_points, grid_mins, grid_maxs, draw=False)
    plt.draw()

def plot_paper(f_tpsn, f_tps, x_ld, u_rd, z_rd, y_md, v_sd, z_sd, x_wp_inds, y_wp_inds, wp_mew=1.5, wp_ms=8):
    l = x_ld.shape[0]
    m = y_md.shape[0]
    x_wp_mask = np.zeros(l, dtype=bool)
    x_wp_mask[x_wp_inds] = True
    y_wp_mask = np.zeros(m, dtype=bool)
    y_wp_mask[y_wp_inds] = True

    plt.ion()
    fig = plt.figure("figure paper", figsize=(12, 9))
    fig.clear()

    axis_mins = np.min(np.r_[x_ld, y_md], axis=0) - 1.5
    axis_maxs = np.max(np.r_[x_ld, y_md], axis=0) + 1.5
    grid_mins = axis_mins - 10
    grid_maxs = axis_maxs + 10

    plt.subplot(221, aspect='equal')
    plt.axis([axis_mins[0], axis_maxs[0], axis_mins[1], axis_maxs[1]])
    plot_warped_grid_2d(lambda pts: pts, grid_mins, grid_maxs, grid_res=1, draw=False)
    plt.plot(x_ld[~x_wp_mask,0], x_ld[~x_wp_mask,1], "r+")
    plt.plot(x_ld[x_wp_mask,0], x_ld[x_wp_mask,1], "r+", mew=wp_mew, ms=wp_ms)
    plot_vectors(u_rd, z_rd, "r-")

    plt.subplot(222, aspect='equal')
    plt.axis([axis_mins[0], axis_maxs[0], axis_mins[1], axis_maxs[1]])
    plot_warped_grid_2d(lambda pts: pts, grid_mins, grid_maxs, grid_res=1, draw=False)
    plt.plot(y_md[~y_wp_mask,0], y_md[~y_wp_mask,1], 'o', markerfacecolor='none', markeredgecolor='b')
    plt.plot(y_md[y_wp_mask,0], y_md[y_wp_mask,1], 'o', markerfacecolor='none', markeredgecolor='b', mew=wp_mew, ms=wp_ms)
    plot_vectors(v_sd, z_sd, "b:", linewidth=2, dashes=(2,2))

    plt.subplot(223, aspect='equal')
    plt.axis([axis_mins[0], axis_maxs[0], axis_mins[1], axis_maxs[1]])
    plot_warped_grid_2d(f_tps.transform_points, grid_mins, grid_maxs, grid_res=1, draw=False)
    xwarped_ld = f_tps.transform_points(x_ld)
    uwarped_rd = np.asarray([f_tps.compute_numerical_jacobian(z_d).dot(u_d) for z_d, u_d in zip(z_rd, u_rd)])
    # uwarped_rd = f_tps.transform_vectors(z_rd, u_rd)
    zwarped_rd = f_tps.transform_points(z_rd)
    plt.plot(xwarped_ld[~x_wp_mask,0],xwarped_ld[~x_wp_mask,1], "r+")
    plt.plot(xwarped_ld[x_wp_mask,0], xwarped_ld[x_wp_mask,1], "r+", mew=wp_mew, ms=wp_ms)
    plot_vectors(uwarped_rd, zwarped_rd, "r-")
    plt.plot(y_md[~y_wp_mask,0], y_md[~y_wp_mask,1], 'o', markerfacecolor='none', markeredgecolor='b')
    plt.plot(y_md[y_wp_mask,0], y_md[y_wp_mask,1], 'o', markerfacecolor='none', markeredgecolor='b', mew=wp_mew, ms=wp_ms)
    plot_vectors(v_sd, z_sd, "b:", linewidth=2, dashes=(2,2))

    plt.subplot(224, aspect='equal')
    plt.axis([axis_mins[0], axis_maxs[0], axis_mins[1], axis_maxs[1]])
    plot_warped_grid_2d(f_tpsn.transform_points, grid_mins, grid_maxs, grid_res=1, draw=False)
    xwarped_ld = f_tpsn.transform_points()
    uwarped_rd = f_tpsn.transform_vectors()
    zwarped_rd = f_tpsn.transform_points(z_rd)
    plt.plot(xwarped_ld[~x_wp_mask,0],xwarped_ld[~x_wp_mask,1], "r+")
    plt.plot(xwarped_ld[x_wp_mask,0], xwarped_ld[x_wp_mask,1], "r+", mew=wp_mew, ms=wp_ms)
    plot_vectors(uwarped_rd, zwarped_rd, "r-")
    plt.plot(y_md[~y_wp_mask,0], y_md[~y_wp_mask,1], 'o', markerfacecolor='none', markeredgecolor='b')
    plt.plot(y_md[y_wp_mask,0], y_md[y_wp_mask,1], 'o', markerfacecolor='none', markeredgecolor='b', mew=wp_mew, ms=wp_ms)
    plot_vectors(v_sd, z_sd, "b:", linewidth=2, dashes=(2,2))

    fig.subplots_adjust(wspace=0.0)
    fig.subplots_adjust(hspace=0.1)

    plt.draw()
    return fig

x_ld, u_rd, z_rd, y_md, v_sd, z_sd, x_wp_inds, y_wp_inds, rad_final, reg_final = generate_problem(args.index)
tpsn_min_param, tps_min_param, tpsn_min_param_ranges, tps_min_param_ranges = get_params(args.index)

if rad_final is None:
    # rad_init = 5
    rad_final = .01
if reg_final is None:
    # reg_init = 1
    reg_final = .1
if tpsn_min_param is None:
    # rad_init, reg_init, radn_init, radn_final, nu_init, nu_final = 0, 10, 0.005, 0.001, 0.01, 0.1

    tpsn_min_cost = float('inf')
    tpsn_min_param = None
    for rad_init in [0.1, 1, 10] if tpsn_min_param_ranges is None else tpsn_min_param_ranges[0]: #[2**i for i in range(-3, 4)]: #[0.1, 1, 10]:
        if rad_final >= rad_init: continue
        for reg_init in [0.1, 1, 10] if tpsn_min_param_ranges is None else tpsn_min_param_ranges[1]: #[2**i for i in range(-3, 4)]: #[0.1, 1, 10]:
            if reg_final >= reg_init: continue
            for radn_init in [0.005, 0.05, 0.5] if tpsn_min_param_ranges is None else tpsn_min_param_ranges[2]: #[2**i for i in range(-7, 0)]: #[0.005, 0.05, 0.5]:
                for radn_final in [0.001, 0.01, 0.1] if tpsn_min_param_ranges is None else tpsn_min_param_ranges[3]: #[2**i for i in range(-10, 2)]: #[0.001, 0.01, 0.1]:
                    if radn_final >= radn_init: continue
                    for nu_init in [0.01, 0.1, 1] if tpsn_min_param_ranges is None else tpsn_min_param_ranges[4]: #[2**i for i in range(-6, 1)]: #[0.01, 0.1, 1]:
                        for nu_final in [0.1, 1, 10] if tpsn_min_param_ranges is None else tpsn_min_param_ranges[5]: #[2**i for i in range(-3, 4)]: #[0.1, 1, 10]:
                            if nu_final <= nu_init: continue
                            f, corr_lm, corr_rs = tps_experimental.tpsn_rpm(x_ld, u_rd, z_rd, y_md, v_sd, z_sd, 
                                reg_init=reg_init, reg_final=reg_final, rad_init=rad_init, rad_final=rad_final, rot_reg=np.r_[1e-4, 1e-4], em_iter=5, callback=None, 
                                radn_init=radn_init, radn_final=radn_final, nu_init=nu_init, nu_final=nu_final)
                            cost = TpsnRpmRegistration.get_objective2(x_ld, u_rd, z_rd, y_md, v_sd, z_sd, f, corr_lm, corr_rs, rad_final, radn_final, reg_final, np.r_[1e-4, 1e-4]).sum()
                            print cost
                            if cost < tpsn_min_cost:
                                tpsn_min_cost = cost
                                tpsn_min_param = (rad_init, reg_init, radn_init, radn_final, nu_init, nu_final)

if tps_min_param is None:
    tps_min_cost = float('inf')
    tps_min_param = None
    for rad_init in [0.1, 1, 10] if tps_min_param_ranges is None else tps_min_param_ranges[0]: #[2**i for i in range(-3, 4)]: #[0.1, 1, 10]:
        if rad_final >= rad_init: continue
        for reg_init in [0.1, 1, 10] if tps_min_param_ranges is None else tps_min_param_ranges[1]: #[2**i for i in range(-3, 4)]: #[0.1, 1, 10]:
            if reg_final >= reg_init: continue
            f, corr_lm = tps.tps_rpm(x_ld, y_md, 
                reg_init=reg_init, reg_final=reg_final, rad_init=rad_init, rad_final=rad_final, rot_reg=np.r_[1e-4, 1e-4], em_iter=5, callback=None)
            cost = TpsRpmRegistration.get_objective2(x_ld, y_md, f, corr_lm, rad_final).sum()
            print cost
            if cost < tps_min_cost:
                tps_min_cost = cost
                tps_min_param = (rad_init, reg_init)

# ipy.embed()

# rad_init, reg_init = tps_min_param #TODO
rad_init, reg_init, radn_init, radn_final, nu_init, nu_final = tpsn_min_param
# rad_init, reg_init, radn_init, radn_final, nu_init, nu_final = 10, 10, 0.005, 0.001, 1, 10
# rad_init, reg_init, radn_init, radn_final, nu_init, nu_final = 1, 10, 0.005, 0.001, 0.01, 1
# rad_init, reg_init, radn_init, radn_final, nu_init, nu_final = 10, 10, 0.5, 0.1, 0.01, 1
f_tpsn, corr_lm, corr_rs = tps_experimental.tpsn_rpm(x_ld, u_rd, z_rd, y_md, v_sd, z_sd, 
                    reg_init=reg_init, reg_final=reg_final, rad_init=rad_init, rad_final=rad_final, rot_reg=np.r_[1e-4, 1e-4], em_iter=5, callback=None, 
                    radn_init=radn_init, radn_final=radn_final, nu_init=nu_init, nu_final=nu_final)

rad_init, reg_init = tps_min_param
# rad_init, reg_init = 10, 1
# rad_init, reg_init = 1, 1
# rad_init, reg_init = 10, 1
f_tps, corr_lm = tps.tps_rpm(x_ld, y_md, 
    reg_init=reg_init, reg_final=reg_final, rad_init=rad_init, rad_final=rad_final, rot_reg=np.r_[1e-4, 1e-4], em_iter=5, callback=None)

print tpsn_min_param
print tps_min_param

fig = plot_paper(f_tpsn, f_tps, x_ld, u_rd, z_rd, y_md, v_sd, z_sd, x_wp_inds, y_wp_inds)
if args.outfile is not None:
    plt.savefig(args.outfile, bbox_inches='tight')

#     for i, (i_start, i_end) in enumerate(zip(pts_segmentation_inds1[:-1], pts_segmentation_inds1[1:])):
#         color = 'r' if len(pts_segmentation_inds1)<=2 else np.tile(np.array(colorsys.hsv_to_rgb(float(i)/(len(pts_segmentation_inds1)-2),1,1)), (i_end-i_start,1))
#         plt.scatter(rope_nodes1[i_start:i_end,0], rope_nodes1[i_start:i_end,1], c=color, edgecolors=color, marker=',', s=1)
    

# u_rd = u_rd[1:,:]
# z_rd = z_rd[1:,:]
# f = ThinPlateSplineNormal(x_ld, u_rd, z_rd, x_ld, u_rd, z_rd)
# tps_experimental.tpsn_fit(f, y_md, v_sd, 1, np.r_[0,0], np.ones(l) * 1, np.ones(l) * 1)
# tpsn_rpm(x_ld, u_rd, z_rd, y_md, v_sd)

# xwarped_ld = f.transform_points()
# uwarped_ld = f.transform_vectors()
# plt.plot(xwarped_ld[:,0], xwarped_ld[:,1], "o")
# plot_vectors(uwarped_ld, xwarped_ld)
# grid_means = .5 * (x_ld.max(axis=0) + x_ld.min(axis=0))
# grid_mins = grid_means - (x_ld.max(axis=0) - x_ld.min(axis=0))
# grid_maxs = grid_means + (x_ld.max(axis=0) - x_ld.min(axis=0))
# plotting_plt.plot_warped_grid_2d(f.transform_points, grid_mins, grid_maxs, draw=False)
# plt.draw()

# import tps
# jac0 = f.compute_numerical_jacobian(x_ld[0])
# jac1 = f.compute_jacobian(x_ld[:2])
# ipy.embed()

# x_ld = np.c_[x_ld, np.raldom.raldom((l,1))]
# y_md = np.c_[y_md, np.raldom.raldom((l,1))]
# f2 = tps.ThinPlateSpline.create_from_optimization(x_ld, y_md, 1.0, 0, None)
# jac0 = f2.compute_jacobian(x_ld)
# jac1 = f2.compute_numerical_jacobian(x_ld[0], epsilon=0.001)

ipy.embed()



