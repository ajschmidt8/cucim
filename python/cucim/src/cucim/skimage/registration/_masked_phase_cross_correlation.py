"""
Implementation of the masked normalized cross-correlation.

Based on the following publication:
D. Padfield. Masked object registration in the Fourier domain.
IEEE Transactions on Image Processing (2012)

and the author's original MATLAB implementation, available on this website:
http://www.dirkpadfield.com/
"""

from functools import partial

import cupy as cp
import numpy as np

from .._shared.fft import fftmodule, next_fast_len


def _masked_phase_cross_correlation(reference_image, moving_image,
                                    reference_mask, moving_mask=None,
                                    overlap_ratio=0.3):
    """Masked image translation registration by masked normalized
    cross-correlation.

    Parameters
    ----------
    reference_image : ndarray
        Reference image.
    moving_image : ndarray
        Image to register. Must be same dimensionality as ``reference_image``,
        but not necessarily the same size.
    reference_mask : ndarray
        Boolean mask for ``reference_image``. The mask should evaluate
        to ``True`` (or 1) on valid pixels. ``reference_mask`` should
        have the same shape as ``reference_image``.
    moving_mask : ndarray or None, optional
        Boolean mask for ``moving_image``. The mask should evaluate to ``True``
        (or 1) on valid pixels. ``moving_mask`` should have the same shape
        as ``moving_image``. If ``None``, ``reference_mask`` will be used.
    overlap_ratio : float, optional
        Minimum allowed overlap ratio between images. The correlation for
        translations corresponding with an overlap ratio lower than this
        threshold will be ignored. A lower `overlap_ratio` leads to smaller
        maximum translation, while a higher `overlap_ratio` leads to greater
        robustness against spurious matches due to small overlap between
        masked images.

    Returns
    -------
    shifts : ndarray
        Shift vector (in pixels) required to register ``moving_image``
        with ``reference_image``. Axis ordering is consistent with
        numpy (e.g. Z, Y, X)

    References
    ----------
    .. [1] Dirk Padfield. Masked Object Registration in the Fourier Domain.
           IEEE Transactions on Image Processing, vol. 21(5),
           pp. 2706-2718 (2012). :DOI:`10.1109/TIP.2011.2181402`
    .. [2] D. Padfield. "Masked FFT registration". In Proc. Computer Vision and
           Pattern Recognition, pp. 2918-2925 (2010).
           :DOI:`10.1109/CVPR.2010.5540032`
    """
    if moving_mask is None:
        if reference_image.shape != moving_image.shape:
            raise ValueError(
                "Input images have different shapes, moving_mask must "
                "be explicitely set.")
        moving_mask = cp.array(reference_mask, dtype=bool, copy=True)

    # We need masks to be of the same size as their respective images
    for (im, mask) in [(reference_image, reference_mask),
                       (moving_image, moving_mask)]:
        if im.shape != mask.shape:
            raise ValueError(
                "Image sizes must match their respective mask sizes.")

    xcorr = cross_correlate_masked(moving_image, reference_image, moving_mask,
                                   reference_mask, axes=(0, 1), mode='full',
                                   overlap_ratio=overlap_ratio)

    # Generalize to the average of multiple equal maxima
    maxima = cp.stack(cp.nonzero(xcorr == xcorr.max()), axis=1)
    center = cp.mean(maxima, axis=0)
    shifts = center - cp.asarray(reference_image.shape) + 1

    # The mismatch in size will impact the center location of the
    # cross-correlation
    size_mismatch = [
        t - s for t, s in zip(moving_image.shape, reference_image.shape)
    ]
    size_mismatch = cp.asarray(size_mismatch)

    return -shifts + (size_mismatch / 2)


def cross_correlate_masked(arr1, arr2, m1, m2, mode='full', axes=(-2, -1),
                           overlap_ratio=0.3):
    """
    Masked normalized cross-correlation between arrays.

    Parameters
    ----------
    arr1 : ndarray
        First array.
    arr2 : ndarray
        Seconds array. The dimensions of `arr2` along axes that are not
        transformed should be equal to that of `arr1`.
    m1 : ndarray
        Mask of `arr1`. The mask should evaluate to `True`
        (or 1) on valid pixels. `m1` should have the same shape as `arr1`.
    m2 : ndarray
        Mask of `arr2`. The mask should evaluate to `True`
        (or 1) on valid pixels. `m2` should have the same shape as `arr2`.
    mode : {'full', 'same'}, optional
        'full':
            This returns the convolution at each point of overlap. At
            the end-points of the convolution, the signals do not overlap
            completely, and boundary effects may be seen.
        'same':
            The output is the same size as `arr1`, centered with respect
            to the `‘full’` output. Boundary effects are less prominent.
    axes : tuple of ints, optional
        Axes along which to compute the cross-correlation.
    overlap_ratio : float, optional
        Minimum allowed overlap ratio between images. The correlation for
        translations corresponding with an overlap ratio lower than this
        threshold will be ignored. A lower `overlap_ratio` leads to smaller
        maximum translation, while a higher `overlap_ratio` leads to greater
        robustness against spurious matches due to small overlap between
        masked images.

    Returns
    -------
    out : ndarray
        Masked normalized cross-correlation.

    Raises
    ------
    ValueError : if correlation `mode` is not valid, or array dimensions along
        non-transformation axes are not equal.

    References
    ----------
    .. [1] Dirk Padfield. Masked Object Registration in the Fourier Domain.
           IEEE Transactions on Image Processing, vol. 21(5),
           pp. 2706-2718 (2012). :DOI:`10.1109/TIP.2011.2181402`
    .. [2] D. Padfield. "Masked FFT registration". In Proc. Computer Vision and
           Pattern Recognition, pp. 2918-2925 (2010).
           :DOI:`10.1109/CVPR.2010.5540032`
    """
    if mode not in {'full', 'same'}:
        raise ValueError("Correlation mode '{}' is not valid.".format(mode))

    if arr1.dtype.kind == "c" or arr2.dtype.kind == "c":
        raise ValueError("complex-valued arr1, arr2 are not supported")

    # CuPy Backend: use float_dtype instead of forcing cp.float64
    float_dtype = np.result_type(arr1.dtype, arr2.dtype, cp.float32)

    fixed_image = arr1.astype(float_dtype, copy=False)
    fixed_mask = m1.astype(bool, copy=False)
    moving_image = arr2.astype(float_dtype, copy=False)
    moving_mask = m2.astype(bool, copy=False)
    eps = np.finfo(float_dtype).eps

    # Array dimensions along non-transformation axes should be equal.
    all_axes = set(range(fixed_image.ndim))
    for axis in (all_axes - set(axes)):
        if fixed_image.shape[axis] != moving_image.shape[axis]:
            raise ValueError(
                "Array shapes along non-transformation axes should be "
                "equal, but dimensions along axis {a} are not".format(a=axis))

    # Determine final size along transformation axes
    # Note that it might be faster to compute Fourier transform in a slightly
    # larger shape (`fast_shape`). Then, after all fourier transforms are done,
    # we slice back to`final_shape` using `final_slice`.
    final_shape = list(arr1.shape)
    for axis in axes:
        final_shape[axis] = fixed_image.shape[axis] + \
            moving_image.shape[axis] - 1
    final_shape = tuple(final_shape)
    final_slice = tuple([slice(0, int(sz)) for sz in final_shape])

    # Extent transform axes to the next fast length (i.e. multiple of 3, 5, or
    # 7)
    fast_shape = tuple([next_fast_len(final_shape[ax]) for ax in axes])

    # We use numpy.fft or the new scipy.fft because they allow leaving the
    # transform axes unchanged which was not possible with scipy.fftpack's
    # fftn/ifftn in older versions of SciPy.
    # E.g. arr shape (2, 3, 7), transform along axes (0, 1) with shape (4, 4)
    # results in arr_fft shape (4, 4, 7)
    fft = partial(fftmodule.fftn, s=fast_shape, axes=axes)
    ifft = partial(fftmodule.ifftn, s=fast_shape, axes=axes)

    fixed_image[cp.logical_not(fixed_mask)] = 0.0
    moving_image[cp.logical_not(moving_mask)] = 0.0

    # N-dimensional analog to rotation by 180deg is flip over all relevant axes.
    # See [1] for discussion.
    rotated_moving_image = _flip(moving_image, axes=axes)
    rotated_moving_mask = _flip(moving_mask, axes=axes)

    fixed_fft = fft(fixed_image)
    rotated_moving_fft = fft(rotated_moving_image)
    fixed_mask_fft = fft(fixed_mask.astype(dtype=float_dtype))
    rotated_moving_mask_fft = fft(rotated_moving_mask.astype(dtype=float_dtype))

    # Calculate overlap of masks at every point in the convolution.
    # Locations with high overlap should not be taken into account.
    number_overlap_masked_px = cp.real(
        ifft(rotated_moving_mask_fft * fixed_mask_fft)
    )
    number_overlap_masked_px[:] = cp.around(number_overlap_masked_px)
    number_overlap_masked_px[:] = cp.fmax(number_overlap_masked_px, eps)
    masked_correlated_fixed_fft = ifft(rotated_moving_mask_fft * fixed_fft)
    masked_correlated_rotated_moving_fft = ifft(
        fixed_mask_fft * rotated_moving_fft)

    numerator = ifft(rotated_moving_fft * fixed_fft)
    numerator -= masked_correlated_fixed_fft * \
        masked_correlated_rotated_moving_fft / number_overlap_masked_px

    fixed_squared_fft = fft(cp.square(fixed_image))
    fixed_denom = ifft(rotated_moving_mask_fft * fixed_squared_fft)
    fixed_denom -= cp.square(masked_correlated_fixed_fft) / \
        number_overlap_masked_px

    fixed_denom[:] = cp.fmax(fixed_denom, 0.0)

    rotated_moving_squared_fft = fft(cp.square(rotated_moving_image))
    moving_denom = ifft(fixed_mask_fft * rotated_moving_squared_fft)
    moving_denom -= cp.square(masked_correlated_rotated_moving_fft) / \
        number_overlap_masked_px

    moving_denom[:] = cp.fmax(moving_denom, 0.0)

    denom = cp.sqrt(fixed_denom * moving_denom)

    # Slice back to expected convolution shape.
    numerator = numerator[final_slice]
    denom = denom[final_slice]
    number_overlap_masked_px = number_overlap_masked_px[final_slice]

    if mode == 'same':
        _centering = partial(_centered,
                             newshape=fixed_image.shape, axes=axes)
        denom = _centering(denom)
        numerator = _centering(numerator)
        number_overlap_masked_px = _centering(number_overlap_masked_px)

    # Pixels where `denom` is very small will introduce large
    # numbers after division. To get around this problem,
    # we zero-out problematic pixels.
    tol = 1e3 * eps * cp.max(cp.abs(denom), axis=axes, keepdims=True)
    nonzero_indices = denom > tol

    # TODO: Added a cast to real here.
    #       probably it should be real earlier?
    numerator = numerator.real
    denom = denom.real
    out = cp.zeros_like(denom)
    out[nonzero_indices] = numerator[nonzero_indices] / denom[nonzero_indices]
    cp.clip(out, a_min=-1, a_max=1, out=out)

    # Apply overlap ratio threshold
    number_px_threshold = overlap_ratio * np.max(number_overlap_masked_px,
                                                 axis=axes, keepdims=True)
    out[number_overlap_masked_px < number_px_threshold] = 0.0

    return out


def _centered(arr, newshape, axes):
    """Return the center `newshape` portion of `arr`, leaving axes not
    in `axes` untouched."""
    currshape = arr.shape

    slices = [slice(None, None)] * arr.ndim

    for ax in axes:
        startind = (currshape[ax] - newshape[ax]) // 2
        endind = startind + newshape[ax]
        slices[ax] = slice(startind, endind)

    return arr[tuple(slices)]


def _flip(arr, axes=None):
    """Reverse array over many axes. Generalization of arr[::-1] for many
    dimensions. If `axes` is `None`, flip along all axes."""
    if axes is None:
        reverse = [slice(None, None, -1)] * arr.ndim
    else:
        reverse = [slice(None, None, None)] * arr.ndim
        for axis in axes:
            reverse[axis] = slice(None, None, -1)

    return arr[tuple(reverse)]
