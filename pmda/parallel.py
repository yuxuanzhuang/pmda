# -*- Mode: python; tab-width: 4; indent-tabs-mode:nil; coding:utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
#
# PMDA
# Copyright (c) 2017 The MDAnalysis Development Team and contributors
# (see the file AUTHORS for the full list of names)
#
# Released under the GNU Public Licence, v2 or any higher version
"""
Parallel Analysis building blocks --- :mod:`MDAnalysis.analysis.base`
=====================================================================

A collection of useful building blocks for creating Analysis
classes.

"""
from __future__ import absolute_import

import inspect
import numpy as np
import MDAnalysis as mda

from joblib import cpu_count

from dask.delayed import delayed
from dask.distributed import Client
import dask
from dask import multiprocessing
from dask.multiprocessing import get


class ParallelAnalysisBase(object):
    """Base class for defining parallel multi frame analysis

    The class it is designed as a template for creating multiframe analyses.
    This class will automatically take care of setting up the trajectory
    reader for iterating, and it offers to show a progress meter.

    To define a new Analysis, `AnalysisBase` needs to be subclassed
    `_single_frame` must be defined. It is also possible to define
    `_prepare` and `_conclude` for pre and post processing. See the example
    below.

    .. code-block:: python

       class NewAnalysis(AnalysisBase):
           def __init__(self, atomgroup, parameter, **kwargs):
               super(NewAnalysis, self).__init__(atomgroup.universe.trajectory,
                                                 **kwargs)
               self._parameter = parameter
               self._ag = atomgroup

           def _prepare(self):
               # OPTIONAL
               # Called before iteration on the trajectory has begun.
               # Data structures can be set up at this time
               self.result = []

           def _single_frame(self):
               # REQUIRED
               # Called after the trajectory is moved onto each new frame.
               # store result of `some_function` for a single frame
               self.result.append(some_function(self._ag, self._parameter))

           def _conclude(self):
               # OPTIONAL
               # Called once iteration on the trajectory is finished.
               # Apply normalisation and averaging to results here.
               self.result = np.asarray(self.result) / np.sum(self.result)

    Afterwards the new analysis can be run like this.

    .. code-block:: python

       na = NewAnalysis(u.select_atoms('name CA'), 35).run()
       print(na.result)

    """

    def __init__(self, universe, atomgroups):
        """
        Parameters
        ----------
        Universe : mda.Universe
            A Universe
        atomgroups : array of mda.AtomGroup
            atomgroups that are iterated in parallel
        """
        self._universe = universe
        self._agroups = atomgroups

    def _conclude(self):
        """Finalise the results you've gathered.

        Called at the end of the run() method to finish everything up.

        In general this method should unpack `self._results` to sensible
        variables

        """
        pass

    def _prepare(self):
        """additional preparation to run"""
        pass

    def _single_frame(self, ts, atomgroups):
        """must return computed values"""
        raise NotImplementedError

    def run(self, n_jobs=1, start=None, stop=None, step=None):
        """Perform the calculation

        Parameters
        ----------
        n_jobs : int, optional
            number of jobs to start, if `-1` use number of logical cpu cores
        start : int, optional
            start frame of analysis
        stop : int, optional
            stop frame of analysis
        step : int, optional
            number of frames to skip between each analysed frame
        """
        if n_jobs == -1:
            n_jobs = cpu_count()

        start, stop, step = self._universe.trajectory.check_slice_indices(
            start, stop, step)
        n_frames = len(range(start, stop, step))

        n_blocks = n_jobs
        bsize = int(np.ceil(n_frames / float(n_blocks)))
        top = self._universe.filename
        traj = self._universe.trajectory.filename
        indices = [ag.indices for ag in self._agroups]

        blocks = []
        for b in range(n_blocks):
            task = delayed(
                self.dask_helper, pure=False)(
                    b * bsize + start,
                    (b + 1) * bsize * step,
                    step,
                    indices,
                    top,
                    traj, )
            blocks.append(task)
        blocks = delayed(blocks)
        self._results = blocks.compute()
        self._conclude()
        return self

    def dask_helper(self, start, stop, step, indices, top, traj):
        """helper function to actually setup dask graph"""
        u = mda.Universe(top, traj)
        agroups = [u.atoms[idx] for idx in indices]
        res = []
        for ts in u.trajectory[start:stop:step]:
            res.append(self._single_frame(ts, agroups))
        return np.asarray(res)