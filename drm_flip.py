"""
Double-buffered DRM page-flip helper for panel-mipi-dbi.

Usage:
    drm = DrmFlip('/dev/dri/card1')
    # drm.width, drm.height give the display dimensions
    # drm.back_buffer is a mmap you write BGR565 bytes into
    drm.back_buffer[:] = pixel_bytes
    drm.flip()   # blocks until the SPI push completes (vsync)
    drm.close()
"""

import ctypes
import fcntl
import mmap
import os
import select


# ── Linux ioctl encoding ───────────────────────────────────────────────────────
def _IOC(dir_, type_, nr, size):
    return (dir_ << 30) | (ord(type_) << 8) | nr | (size << 16)

def _IOWR(t, n, s): return _IOC(3, t, n, s)
def _IOW(t, n, s):  return _IOC(1, t, n, s)
def _IOR(t, n, s):  return _IOC(2, t, n, s)


# ── DRM structures ─────────────────────────────────────────────────────────────
class _CardRes(ctypes.Structure):
    _fields_ = [
        ('fb_id_ptr',        ctypes.c_uint64),
        ('crtc_id_ptr',      ctypes.c_uint64),
        ('connector_id_ptr', ctypes.c_uint64),
        ('encoder_id_ptr',   ctypes.c_uint64),
        ('count_fbs',        ctypes.c_uint32),
        ('count_crtcs',      ctypes.c_uint32),
        ('count_connectors', ctypes.c_uint32),
        ('count_encoders',   ctypes.c_uint32),
        ('min_width',        ctypes.c_uint32),
        ('max_width',        ctypes.c_uint32),
        ('min_height',       ctypes.c_uint32),
        ('max_height',       ctypes.c_uint32),
    ]

class _GetConnector(ctypes.Structure):
    _fields_ = [
        ('encoders_ptr',      ctypes.c_uint64),
        ('modes_ptr',         ctypes.c_uint64),
        ('props_ptr',         ctypes.c_uint64),
        ('prop_values_ptr',   ctypes.c_uint64),
        ('count_modes',       ctypes.c_uint32),
        ('count_props',       ctypes.c_uint32),
        ('count_encoders',    ctypes.c_uint32),
        ('encoder_id',        ctypes.c_uint32),
        ('connector_id',      ctypes.c_uint32),
        ('connector_type',    ctypes.c_uint32),
        ('connector_type_id', ctypes.c_uint32),
        ('connection',        ctypes.c_uint32),
        ('mm_width',          ctypes.c_uint32),
        ('mm_height',         ctypes.c_uint32),
        ('subpixel',          ctypes.c_uint32),
        ('pad',               ctypes.c_uint32),
    ]

class _ModeInfo(ctypes.Structure):
    _fields_ = [
        ('clock',       ctypes.c_uint32),
        ('hdisplay',    ctypes.c_uint16),
        ('hsync_start', ctypes.c_uint16),
        ('hsync_end',   ctypes.c_uint16),
        ('htotal',      ctypes.c_uint16),
        ('hskew',       ctypes.c_uint16),
        ('vdisplay',    ctypes.c_uint16),
        ('vsync_start', ctypes.c_uint16),
        ('vsync_end',   ctypes.c_uint16),
        ('vtotal',      ctypes.c_uint16),
        ('vscan',       ctypes.c_uint16),
        ('vrefresh',    ctypes.c_uint32),
        ('flags',       ctypes.c_uint32),
        ('type',        ctypes.c_uint32),
        ('name',        ctypes.c_char * 32),
    ]

class _CreateDumb(ctypes.Structure):
    _fields_ = [
        ('height', ctypes.c_uint32),
        ('width',  ctypes.c_uint32),
        ('bpp',    ctypes.c_uint32),
        ('flags',  ctypes.c_uint32),
        ('handle', ctypes.c_uint32),
        ('pitch',  ctypes.c_uint32),
        ('size',   ctypes.c_uint64),
    ]

class _MapDumb(ctypes.Structure):
    _fields_ = [
        ('handle', ctypes.c_uint32),
        ('pad',    ctypes.c_uint32),
        ('offset', ctypes.c_uint64),
    ]

class _FbCmd(ctypes.Structure):
    _fields_ = [
        ('fb_id',  ctypes.c_uint32),
        ('width',  ctypes.c_uint32),
        ('height', ctypes.c_uint32),
        ('pitch',  ctypes.c_uint32),
        ('bpp',    ctypes.c_uint32),
        ('depth',  ctypes.c_uint32),
        ('handle', ctypes.c_uint32),
    ]

class _SetCrtc(ctypes.Structure):
    _fields_ = [
        ('set_connectors_ptr', ctypes.c_uint64),
        ('count_connectors',   ctypes.c_uint32),
        ('crtc_id',            ctypes.c_uint32),
        ('fb_id',              ctypes.c_uint32),
        ('x',                  ctypes.c_uint32),
        ('y',                  ctypes.c_uint32),
        ('gamma_size',         ctypes.c_uint32),
        ('mode_valid',         ctypes.c_uint32),
        ('mode',               _ModeInfo),
    ]

class _PageFlip(ctypes.Structure):
    _fields_ = [
        ('crtc_id',   ctypes.c_uint32),
        ('fb_id',     ctypes.c_uint32),
        ('flags',     ctypes.c_uint32),
        ('reserved',  ctypes.c_uint32),
        ('user_data', ctypes.c_uint64),
    ]


# ── ioctl numbers ──────────────────────────────────────────────────────────────
_MODE_GETRESOURCES = _IOWR('d', 0xA0, ctypes.sizeof(_CardRes))
_MODE_GETCONNECTOR = _IOWR('d', 0xA7, ctypes.sizeof(_GetConnector))
_MODE_CREATE_DUMB  = _IOWR('d', 0xB2, ctypes.sizeof(_CreateDumb))
_MODE_MAP_DUMB     = _IOWR('d', 0xB3, ctypes.sizeof(_MapDumb))
_MODE_ADDFB        = _IOWR('d', 0xAE, ctypes.sizeof(_FbCmd))
_MODE_SETCRTC      = _IOWR('d', 0xA2, ctypes.sizeof(_SetCrtc))
_MODE_PAGE_FLIP    = _IOWR('d', 0xB0, ctypes.sizeof(_PageFlip))

_PAGE_FLIP_EVENT   = 0x01
_EVENT_FLIP_COMPLETE = 0x02
_DRM_CONNECTED     = 1


class DrmFlip:
    """Double-buffered DRM display using dumb buffers and page-flip vsync."""

    def __init__(self, dev='/dev/dri/card1'):
        self._fd = os.open(dev, os.O_RDWR | os.O_CLOEXEC)
        self._handles = []
        self._maps = []
        self._fb_ids = []
        self._back = 1
        self._front = 0
        self._flip_pending = False

        self._enumerate()
        self._create_buffers()
        self._set_crtc(self._fb_ids[0])

    # ── setup ──────────────────────────────────────────────────────────────────
    def _ioctl(self, req, arg):
        fcntl.ioctl(self._fd, req, arg)

    def _enumerate(self):
        res = _CardRes()
        self._ioctl(_MODE_GETRESOURCES, res)

        conn_arr = (ctypes.c_uint32 * res.count_connectors)()
        crtc_arr = (ctypes.c_uint32 * res.count_crtcs)()
        enc_arr  = (ctypes.c_uint32 * max(res.count_encoders, 1))()
        res.connector_id_ptr = ctypes.addressof(conn_arr)
        res.crtc_id_ptr      = ctypes.addressof(crtc_arr)
        res.encoder_id_ptr   = ctypes.addressof(enc_arr)
        self._ioctl(_MODE_GETRESOURCES, res)

        self._crtc_id = int(crtc_arr[0])

        for i in range(res.count_connectors):
            conn = _GetConnector(connector_id=int(conn_arr[i]))
            self._ioctl(_MODE_GETCONNECTOR, conn)

            if conn.connection != _DRM_CONNECTED:
                continue

            modes     = (_ModeInfo      * conn.count_modes)()
            enc_ids   = (ctypes.c_uint32 * max(conn.count_encoders, 1))()
            props     = (ctypes.c_uint32 * max(conn.count_props, 1))()
            prop_vals = (ctypes.c_uint64 * max(conn.count_props, 1))()
            conn.modes_ptr       = ctypes.addressof(modes)
            conn.encoders_ptr    = ctypes.addressof(enc_ids)
            conn.props_ptr       = ctypes.addressof(props)
            conn.prop_values_ptr = ctypes.addressof(prop_vals)
            self._ioctl(_MODE_GETCONNECTOR, conn)

            self._connector_id = int(conn_arr[i])
            self._mode = _ModeInfo()
            ctypes.memmove(ctypes.addressof(self._mode),
                           ctypes.addressof(modes[0]),
                           ctypes.sizeof(_ModeInfo))
            self.width  = int(self._mode.hdisplay)
            self.height = int(self._mode.vdisplay)
            return

        raise RuntimeError('No connected DRM connector found on ' + str(self._fd))

    def _create_buffers(self):
        for _ in range(2):
            dumb = _CreateDumb(height=self.height, width=self.width, bpp=16)
            self._ioctl(_MODE_CREATE_DUMB, dumb)
            self._handles.append(int(dumb.handle))

            fb = _FbCmd(width=self.width, height=self.height,
                        pitch=int(dumb.pitch), bpp=16, depth=16,
                        handle=int(dumb.handle))
            self._ioctl(_MODE_ADDFB, fb)
            self._fb_ids.append(int(fb.fb_id))

            md = _MapDumb(handle=int(dumb.handle))
            self._ioctl(_MODE_MAP_DUMB, md)

            buf = mmap.mmap(self._fd, int(dumb.size),
                            mmap.MAP_SHARED, mmap.PROT_WRITE,
                            offset=int(md.offset))
            self._maps.append(buf)

    def _set_crtc(self, fb_id):
        conn_arr = (ctypes.c_uint32 * 1)(self._connector_id)
        crtc = _SetCrtc(
            crtc_id=self._crtc_id,
            fb_id=fb_id,
            count_connectors=1,
            mode_valid=1,
            mode=self._mode,
        )
        crtc.set_connectors_ptr = ctypes.addressof(conn_arr)
        self._ioctl(_MODE_SETCRTC, crtc)

    # ── public API ─────────────────────────────────────────────────────────────
    @property
    def back_buffer(self):
        """mmap of the buffer currently being written to (not displayed)."""
        return self._maps[self._back]

    def begin_flip(self):
        """
        Submit the back buffer for display and return immediately.

        The SPI transfer runs in the background. Call wait_flip() before the
        next write to back_buffer to ensure the kernel has finished reading the
        old front buffer (which becomes the new back buffer after this call).
        """
        flip = _PageFlip(
            crtc_id=self._crtc_id,
            fb_id=self._fb_ids[self._back],
            flags=_PAGE_FLIP_EVENT,
            user_data=0,
        )
        self._ioctl(_MODE_PAGE_FLIP, flip)
        self._flip_pending = True
        self._front, self._back = self._back, self._front

    def wait_flip(self):
        """Block until the in-flight SPI transfer completes."""
        if not self._flip_pending:
            return
        rlist, _, _ = select.select([self._fd], [], [], 2.0)
        if rlist:
            os.read(self._fd, 64)
        self._flip_pending = False

    def flip(self):
        """Blocking flip: submit back buffer and wait for SPI to finish."""
        self.begin_flip()
        self.wait_flip()

    def close(self):
        for buf in self._maps:
            try:
                buf.close()
            except Exception:
                pass
        os.close(self._fd)
