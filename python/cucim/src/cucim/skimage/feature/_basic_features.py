import functools
import itertools
import math
from itertools import combinations_with_replacement

import cupy as cp
import numpy as np

from cucim.skimage import feature, filters, img_as_float32


def _texture_filter(gaussian_filtered):
    combos = combinations_with_replacement
    H_elems = [
        cp.gradient(cp.gradient(gaussian_filtered)[ax0], axis=ax1)
        for ax0, ax1 in combos(range(gaussian_filtered.ndim), 2)
    ]
    eigvals = feature.hessian_matrix_eigvals(H_elems)
    return eigvals


def _singlescale_basic_features_singlechannel(
    img, sigma, intensity=True, edges=True, texture=True
):
    results = ()
    gaussian_filtered = filters.gaussian(img, sigma)
    if intensity:
        results += (gaussian_filtered,)
    if edges:
        results += (filters.sobel(gaussian_filtered),)
    if texture:
        results += (*_texture_filter(gaussian_filtered),)
    return results


def _mutiscale_basic_features_singlechannel(
    img,
    intensity=True,
    edges=True,
    texture=True,
    sigma_min=0.5,
    sigma_max=16,
    num_sigma=None,
    num_workers=None,
):
    """Features for a single channel nd image.

    Parameters
    ----------
    img : ndarray
        Input image, which can be grayscale or multichannel.
    intensity : bool, default True
        If True, pixel intensities averaged over the different scales
        are added to the feature set.
    edges : bool, default True
        If True, intensities of local gradients averaged over the different
        scales are added to the feature set.
    texture : bool, default True
        If True, eigenvalues of the Hessian matrix after Gaussian blurring
        at different scales are added to the feature set.
    sigma_min : float, optional
        Smallest value of the Gaussian kernel used to average local
        neighbourhoods before extracting features.
    sigma_max : float, optional
        Largest value of the Gaussian kernel used to average local
        neighbourhoods before extracting features.
    num_sigma : int, optional
        Number of values of the Gaussian kernel between sigma_min and sigma_max.
        If None, sigma_min multiplied by powers of 2 are used.
    num_workers : int or None, optional
        The number of parallel threads to use. If set to ``None``, the full
        set of available cores are used.

    Returns
    -------
    features : list
        List of features, each element of the list is an array of shape as img.
    """
    # computations are faster as float32
    img = cp.ascontiguousarray(img_as_float32(img))
    if num_sigma is None:
        num_sigma = int(math.log2(sigma_max) - math.log2(sigma_min) + 1)
    sigmas = np.logspace(
        math.log2(sigma_min),
        math.log2(sigma_max),
        num=num_sigma,
        base=2,
        endpoint=True,
    )
    singlescale_func = functools.partial(
        _singlescale_basic_features_singlechannel,
        intensity=intensity, edges=edges, texture=texture
    )
    out_sigmas = [singlescale_func(img, s) for s in sigmas]
    features = itertools.chain.from_iterable(out_sigmas)
    return features


def multiscale_basic_features(
    image,
    multichannel=False,
    intensity=True,
    edges=True,
    texture=True,
    sigma_min=0.5,
    sigma_max=16,
    num_sigma=None,
    num_workers=None,
):
    """Local features for a single- or multi-channel nd image.

    Intensity, gradient intensity and local structure are computed at
    different scales thanks to Gaussian blurring.

    Parameters
    ----------
    image : ndarray
        Input image, which can be grayscale or multichannel.
    multichannel : bool, default False
        True if the last dimension corresponds to color channels.
    intensity : bool, default True
        If True, pixel intensities averaged over the different scales
        are added to the feature set.
    edges : bool, default True
        If True, intensities of local gradients averaged over the different
        scales are added to the feature set.
    texture : bool, default True
        If True, eigenvalues of the Hessian matrix after Gaussian blurring
        at different scales are added to the feature set.
    sigma_min : float, optional
        Smallest value of the Gaussian kernel used to average local
        neighbourhoods before extracting features.
    sigma_max : float, optional
        Largest value of the Gaussian kernel used to average local
        neighbourhoods before extracting features.
    num_sigma : int, optional
        Number of values of the Gaussian kernel between sigma_min and sigma_max.
        If None, sigma_min multiplied by powers of 2 are used.
    num_workers : int or None, optional
        The number of parallel threads to use. If set to ``None``, the full
        set of available cores are used.


    Returns
    -------
    features : cp.ndarray
        Array of shape ``image.shape + (n_features,)``
    """
    if not any([intensity, edges, texture]):
        raise ValueError(
            "At least one of ``intensity``, ``edges`` or ``textures``"
            "must be True for features to be computed."
        )
    if image.ndim < 3:
        multichannel = False
    if not multichannel:
        image = image[..., cp.newaxis]
    all_results = (
        _mutiscale_basic_features_singlechannel(
            image[..., dim],
            intensity=intensity,
            edges=edges,
            texture=texture,
            sigma_min=sigma_min,
            sigma_max=sigma_max,
            num_sigma=num_sigma,
            num_workers=num_workers,
        )
        for dim in range(image.shape[-1])
    )
    features = list(itertools.chain.from_iterable(all_results))
    return cp.stack(features, axis=-1)
