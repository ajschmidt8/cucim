"""Miscellaneous morphology functions."""
import functools

import cupy as cp
from cupyx.scipy import ndimage as ndi

from .._shared.utils import warn
from .selem import _default_selem

# Our function names don't exactly correspond to ndimages.
# This dictionary translates from our names to scipy's.
funcs = ('erosion', 'dilation', 'opening', 'closing')
skimage2ndimage = {x: 'grey_' + x for x in funcs}

# These function names are the same in ndimage.
funcs = ('binary_erosion', 'binary_dilation', 'binary_opening',
         'binary_closing', 'black_tophat', 'white_tophat')
skimage2ndimage.update({x: x for x in funcs})


def default_selem(func):
    """Decorator to add a default structuring element to morphology functions.

    Parameters
    ----------
    func : function
        A morphology function such as erosion, dilation, opening, closing,
        white_tophat, or black_tophat.

    Returns
    -------
    func_out : function
        The function, using a default structuring element of same dimension
        as the input image with connectivity 1.

    """

    @functools.wraps(func)
    def func_out(image, selem=None, *args, **kwargs):
        if selem is None:
            selem = _default_selem(image.ndim)
        return func(image, selem=selem, *args, **kwargs)

    return func_out


def _check_dtype_supported(ar):
    # Should use `issubdtype` for bool below, but there's a bug in numpy 1.7
    if not (ar.dtype == bool or cp.issubdtype(ar.dtype, cp.integer)):
        raise TypeError("Only bool or integer image types are supported. "
                        "Got %s." % ar.dtype)


def remove_small_objects(ar, min_size=64, connectivity=1, in_place=False):
    """Remove objects smaller than the specified size.

    Expects ar to be an array with labeled objects, and removes objects
    smaller than min_size. If `ar` is bool, the image is first labeled.
    This leads to potentially different behavior for bool and 0-and-1
    arrays.

    Parameters
    ----------
    ar : ndarray (arbitrary shape, int or bool type)
        The array containing the objects of interest. If the array type is
        int, the ints must be non-negative.
    min_size : int, optional (default: 64)
        The smallest allowable object size.
    connectivity : int, {1, 2, ..., ar.ndim}, optional (default: 1)
        The connectivity defining the neighborhood of a pixel. Used during
        labelling if `ar` is bool.
    in_place : bool, optional (default: False)
        If ``True``, remove the objects in the input array itself.
        Otherwise, make a copy.

    Raises
    ------
    TypeError
        If the input array is of an invalid type, such as float or string.
    ValueError
        If the input array contains negative values.

    Returns
    -------
    out : ndarray, same shape and type as input `ar`
        The input array with small connected components removed.

    Examples
    --------
    >>> import cupy as cp
    >>> from cucim.skimage import morphology
    >>> a = cp.array([[0, 0, 0, 1, 0],
    ...               [1, 1, 1, 0, 0],
    ...               [1, 1, 1, 0, 1]], bool)
    >>> b = morphology.remove_small_objects(a, 6)
    >>> b
    array([[False, False, False, False, False],
           [ True,  True,  True, False, False],
           [ True,  True,  True, False, False]])
    >>> c = morphology.remove_small_objects(a, 7, connectivity=2)
    >>> c
    array([[False, False, False,  True, False],
           [ True,  True,  True, False, False],
           [ True,  True,  True, False, False]])
    >>> d = morphology.remove_small_objects(a, 6, in_place=True)
    >>> d is a
    True

    """
    # Raising type error if not int or bool
    _check_dtype_supported(ar)

    if in_place:
        out = ar
    else:
        out = ar.copy()

    if min_size == 0:  # shortcut for efficiency
        return out

    if out.dtype == bool:
        selem = ndi.generate_binary_structure(ar.ndim, connectivity)
        ccs = cp.zeros_like(ar, dtype=cp.int32)
        ndi.label(ar, selem, output=ccs)
    else:
        ccs = out

    try:
        component_sizes = cp.bincount(ccs.ravel())
    except ValueError:
        raise ValueError("Negative value labels are not supported. Try "
                         "relabeling the input with `scipy.ndimage.label` or "
                         "`skimage.morphology.label`.")

    if len(component_sizes) == 2 and out.dtype != bool:
        warn("Only one label was provided to `remove_small_objects`. "
             "Did you mean to use a boolean array?")

    too_small = component_sizes < min_size
    too_small_mask = too_small[ccs]
    out[too_small_mask] = 0

    return out


def remove_small_holes(ar, area_threshold=64, connectivity=1, in_place=False):
    """Remove contiguous holes smaller than the specified size.

    Parameters
    ----------
    ar : ndarray (arbitrary shape, int or bool type)
        The array containing the connected components of interest.
    area_threshold : int, optional (default: 64)
        The maximum area, in pixels, of a contiguous hole that will be filled.
        Replaces `min_size`.
    connectivity : int, {1, 2, ..., ar.ndim}, optional (default: 1)
        The connectivity defining the neighborhood of a pixel.
    in_place : bool, optional (default: False)
        If `True`, remove the connected components in the input array itself.
        Otherwise, make a copy.

    Raises
    ------
    TypeError
        If the input array is of an invalid type, such as float or string.
    ValueError
        If the input array contains negative values.

    Returns
    -------
    out : ndarray, same shape and type as input `ar`
        The input array with small holes within connected components removed.

    Examples
    --------
    >>> import cupy as cp
    >>> from cucim.skimage import morphology
    >>> a = cp.array([[1, 1, 1, 1, 1, 0],
    ...               [1, 1, 1, 0, 1, 0],
    ...               [1, 0, 0, 1, 1, 0],
    ...               [1, 1, 1, 1, 1, 0]], bool)
    >>> b = morphology.remove_small_holes(a, 2)
    >>> b
    array([[ True,  True,  True,  True,  True, False],
           [ True,  True,  True,  True,  True, False],
           [ True, False, False,  True,  True, False],
           [ True,  True,  True,  True,  True, False]])
    >>> c = morphology.remove_small_holes(a, 2, connectivity=2)
    >>> c
    array([[ True,  True,  True,  True,  True, False],
           [ True,  True,  True, False,  True, False],
           [ True, False, False,  True,  True, False],
           [ True,  True,  True,  True,  True, False]])
    >>> d = morphology.remove_small_holes(a, 2, in_place=True)
    >>> d is a
    True

    Notes
    -----
    If the array type is int, it is assumed that it contains already-labeled
    objects. The labels are not kept in the output image (this function always
    outputs a bool image). It is suggested that labeling is completed after
    using this function.

    """
    _check_dtype_supported(ar)

    # Creates warning if image is an integer image
    if ar.dtype != bool:
        warn("Any labeled images will be returned as a boolean array. "
             "Did you mean to use a boolean array?", UserWarning)

    if in_place:
        out = ar
    else:
        out = ar.copy()

    # Creating the inverse of ar
    if in_place:
        cp.logical_not(out, out=out)
    else:
        out = cp.logical_not(out)

    # removing small objects from the inverse of ar
    out = remove_small_objects(out, area_threshold, connectivity, in_place)

    if in_place:
        cp.logical_not(out, out=out)
    else:
        out = cp.logical_not(out)

    return out
