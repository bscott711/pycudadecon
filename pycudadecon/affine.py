from .util import load_lib

import ctypes
import numpy as np
import logging

logger = logging.getLogger(__name__)


cudaLib = load_lib("libcudaDeconv")

if not cudaLib:
    logger.error("Could not load libcudaDeconv!")
else:
    try:
        # https://stackoverflow.com/questions/5862915/passing-numpy-arrays-to-a-c-function-for-input-and-output
        Deskew_interface = cudaLib.Deskew_interface
        Deskew_interface.restype = ctypes.c_int
        Deskew_interface.argtypes = [
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_float,
        ]

        # Full Affine transformation
        Affine_interface = cudaLib.Affine_interface
        Affine_interface.restype = ctypes.c_int
        Affine_interface.argtypes = [
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
        ]

        # Affine transformation with a spatial referencing object
        Affine_interface_RA = cudaLib.Affine_interface_RA
        Affine_interface_RA.restype = ctypes.c_int
        Affine_interface_RA.argtypes = [
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(ctypes.c_float, flags="C_CONTIGUOUS"),
        ]

    except AttributeError as e:
        logger.warning("Failed to properly import libcudaDeconv")
        print(e)


def deskewGPU(im, dxdata=0.1, dzdata=0.5, angle=31.5, width=0, shift=0, pad_val="auto"):
    """Deskew data acquired in stage-scanning mode on GPU

    Simple affine transform variant to perform a shear operation to correct
    for volume shearing.

    Args:
        im (np.ndarray): Image volume to deskew
        dxdata (float): XY Pixel size of image volume (default: {0.1})
        dzdata (float): Z-step size in image volume.  In a typical light sheet
            stage-scanning aquisition, this corresponds to the step
            size that the stage takes between planes, NOT the final
            Z-step size between planeds after deskewing along the optical
            axis of the detection objective (default: {0.5})
        angle (float): Deskew angle (usually, angle between sheet and axis of
            stage motion) (default: {31.5})
        width (int): If not 0, crop output image to specified width (default: {0})
        shift (int): If not 0, shift image center by this value (default: {0})
        pad_val (int): Value to pad image with when deskewing.  If 'auto'
            the median value of the last Z plane will be used. (default: {'auto'})

    Returns:
        np.ndarray: Deskewed volume
    """
    if isinstance(pad_val, str) and pad_val == "auto":
        pad_val = np.median(im[-1])
    assert isinstance(pad_val, (int, float))

    nz, ny, nx = im.shape
    _dtype = im.dtype
    if not np.issubdtype(_dtype, np.float32):
        im = im.astype(np.float32)

    # have to calculate this here to know the size of the return array
    if width == 0:
        deskewedNx = np.int(
            nx + np.floor(nz * dzdata * abs(np.cos(angle * np.pi / 180)) / dxdata)
        )
    else:
        deskewedNx = width

    result = np.empty((nz, ny, deskewedNx), dtype=np.float32)
    Deskew_interface(
        im, nx, ny, nz, dzdata, dxdata, angle, result, deskewedNx, shift, pad_val
    )
    return result.astype(_dtype)


def affineGPU(im, tmat, dzyx=None):
    """Perform 3D affine transformation of image given a 4x4 transformation matrix

    optional dzyx parameter {tuple, list} specifies the voxel size of the
    image [dz, dy, dx]. If it is provided, it will be used to transform
    the image from intrinsic coordinates to world coordinates prior to
    transformation, e.g.:

    *x = 0.5 + (x - 0.5) * dx*

    and then back to intrinsic coords afterwards... e.g.:

    *tu = 0.5 + (tu - 0.5) / dx*


    Args:
        im (np.ndarray): 3D input volume
        tmat (np.ndarray): Affine transformation matrix
        dzyx (list): Voxel size of input volume ([dz, dy, dx]).  If provided, the
            transformation matrix is assumed to be in units of sample space.
            otherwise the transformation is performed in image coordinates
            (default: {None})

    Returns:
        np.ndarray: Transformed volume

    Raises:
        ValueError: If the dimensions of the transformation matrix do not
            the input volume.  For instance, a 3D input volume requires a
            4 x 4 tranforamtion matrix.

    Examples:

        Perform simple translation

        >>> nx, ny, nz = (10, 20, 3)
        >>> T = np.array([[1, 0, 0, nx],
        ...               [0, 1, 0, ny],
        ...               [0, 0, 1, nz],
        ...               [0, 0, 0, 1]])
        >>> rotated = affineGPU(im, T)

        Perform a rotation about the Y axis...
        (this is the underlying code for :func:`rotateGPU`)

        >>> theta = angle * np.pi / 180
        >>> nz, ny, nx = im.shape
        >>> xzRatio = dxdata / (np.deg2rad(angle) * dzdata)
        >>> # first translate the middle of the image to the origin
        >>> T1 = np.array([[1, 0, 0, nx / 2],
        >>>                [0, 1, 0, ny / 2],
        >>>                [0, 0, 1, nz / 2],
        >>>                [0, 0, 0, 1]])
        >>> # then scale (resample) the Z axis the dz/dx ratio
        >>> S = np.array([[1, 0, 0, 0],
        >>>               [0, 1, 0, 0],
        >>>               [0, 0, xzRatio, 0],
        >>>               [0, 0, 0, 1]])
        >>> # then rotate theta degrees about the Y axis
        >>> R = np.array([[np.cos(theta), 0, -np.sin(theta), 0],
        >>>               [0, 1, 0, 0],
        >>>               [np.sin(theta), 0, np.cos(theta), 0],
        >>>               [0, 0, 0, 1]])
        >>> # then translate back to the original origin
        >>> T2 = np.array([[1, 0, 0, -nx / 2],
        >>>                [0, 1, 0, -ny / 2],
        >>>                [0, 0, 1, -nz / 2],
        >>>                [0, 0, 0, 1]])
        >>> T = np.eye(4)
        >>> T = np.dot(np.dot(np.dot(np.dot(T, T1), S), R), T2)
        >>> rotated = affineGPU(im, T)

    """
    if not tmat.shape == tuple([im.ndim + 1] * 2):
        raise ValueError(
            "{} dimensional transform matrix used on {} dimensional image".format(
                tmat.shape[0] - 1, im.ndim
            )
        )

    nz, ny, nx = im.shape
    if not np.issubdtype(im.dtype, np.float32) or not im.flags["C_CONTIGUOUS"]:
        im = np.ascontiguousarray(im, dtype=np.float32)
    if not np.issubdtype(tmat.dtype, np.float32):
        tmat = tmat.astype(np.float32)
    # have to calculate this here to know the size of the return array
    result = np.empty((nz, ny, nx), dtype=np.float32)
    if isinstance(dzyx, (tuple, list)) and len(dzyx) == 3:
        dzyx = [float(i) for i in dzyx]
        # note, dzyx coordinate order is flipped when handing to Affine_interface_RA
        Affine_interface_RA(im, nx, ny, nz, dzyx[2], dzyx[1], dzyx[0], result, tmat)
    else:
        Affine_interface(im, nx, ny, nz, result, tmat)
    return result


def rotateGPU(im, dzdata, dxdata=0.1, angle=31.5, reverse=False):
    """Rotate image around Y axis by some angle

    This is a convenience function that will apply the appropriate affine
    transformation for rotating a volume around the Y axis by some angle.
    This is typically done with images acquired on inverted light sheet
    microscopes where the image plane is not parallel to the coverslip
    (such as lattice light sheet, or diSPIM microscopes), in order to change
    the coordinate system of the image volume such that the Z axis is
    orthogonal to the coverslip

    Args:
        im (np.ndarray): 3D volume to be rotated
        dzdata (float): Z-step size in microns of the image volume
        dxdata (float): XY pixel size of the volume (default: {0.1})
        angle (float): Angle to rotate around Y axis (default: {31.5})
        reverse (bool): Rotate in the opposite direction. (default: {False})

    Returns:
        np.ndarray: Rotated 3D volume
    """
    angle = float(angle)
    xzRatio = dxdata / (np.deg2rad(angle) * dzdata)

    npad = ((0, 0), (0, 0), (0, 0))
    im = np.pad(im, pad_width=npad, mode="constant", constant_values=0)

    theta = angle * np.pi / 180
    theta = theta if not reverse else -theta

    nz, ny, nx = im.shape
    # first translate the middle of the image to the origin
    T1 = np.array(
        [[1, 0, 0, nx / 2], [0, 1, 0, ny / 2], [0, 0, 1, nz / 2], [0, 0, 0, 1]]
    )
    # then scale (resample) the Z axis the dz/dx ratio
    S = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, xzRatio, 0], [0, 0, 0, 1]])
    # then rotate theta degrees about the Y axis
    R = np.array(
        [
            [np.cos(theta), 0, -np.sin(theta), 0],
            [0, 1, 0, 0],
            [np.sin(theta), 0, np.cos(theta), 0],
            [0, 0, 0, 1],
        ]
    )
    # then translate back to the original origin
    T2 = np.array(
        [[1, 0, 0, -nx / 2], [0, 1, 0, -ny / 2], [0, 0, 1, -nz / 2], [0, 0, 0, 1]]
    )
    T = np.eye(4)
    T = np.dot(np.dot(np.dot(np.dot(T, T1), S), R), T2)

    rotated = affineGPU(im, T)

    return rotated
