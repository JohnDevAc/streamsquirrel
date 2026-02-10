import threading
import time
import random
from typing import Optional

from ndi_backend import NDIReceiver
from aes67_rtp import RTPAES67Sender
from sap import SAPAnnouncer, build_sdp
from config import SAP_INTERVAL_SEC, SAP_SRC_IP_ENV
import os

class SlotPipeline:
    def __init__(self, slot_id: int, ndi_source_name: str, aes67_name: str, mcast_ip: str, mcast_port: int):
        self.slot_id = slot_id
        self.ndi_source_name = ndi_source_name
        # Mirror the NDI name if the UI didn't provide a separate AES67 name
        self.aes67_name = (aes67_name or "").strip() or ndi_source_name
        self.mcast_ip = mcast_ip
        self.mcast_port = mcast_port

        self._thread: Optional[threading.Thread] = None
        self._sap_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._error: Optional[str] = None
        self.audio_frames = 0
        self.rtp_packets = 0
        self.rtp_packets_mon = 0
        self.last_exception = ""

        self._receiver = NDIReceiver(ndi_source_name)
        self._rtp = RTPAES67Sender(mcast_ip, mcast_port, ssrc=random.randint(1, 0xFFFFFFFF))

        origin_ip = os.getenv(SAP_SRC_IP_ENV, "").strip() or "0.0.0.0"
        sdp, ident = build_sdp(self.aes67_name, mcast_ip, mcast_port, payload_type=96, codec="L24", origin_ip=origin_ip)
        self._sap = SAPAnnouncer(sdp, ident)


    def start(self):
        """Start NDI receiver, RTP sender loop and SAP announcer."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._receiver.connect()
        # SAP announcements should continue even if the NDI audio stream stalls,
        # so run them in a dedicated loop.
        self._sap_thread = threading.Thread(target=self._sap_loop, daemon=True)
        self._sap_thread.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

        # Try to withdraw (SAP delete) before closing sockets.
        try:
            self._sap.send_delete_burst()
        except Exception:
            pass

        try:
            self._receiver.close()
        except Exception:
            pass
        try:
            self._rtp.close()
        except Exception:
            pass
        try:
            self._sap.close()
        except Exception:
            pass

    def _run(self):

        try:
            for (pcm_i32_le, samples_per_channel) in self._receiver.read_audio():
                if self._stop.is_set():
                    break

                self.audio_frames += 1
                self._rtp.send_int32le_frame(pcm_i32_le, samples_per_channel)
                self.rtp_packets = getattr(self._rtp, 'packets_sent', self.rtp_packets)


        except Exception as e:
            self._error = str(e)
            self.last_exception = str(e)
            print(f"[PIPELINE] slot {self.slot_id} error: {e}")

    def _sap_loop(self):
        """Periodically announce SDP via SAP for the lifetime of the pipeline."""
        next_send = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now >= next_send:
                try:
                    self._sap.send_once()
                except Exception as e:
                    self.last_exception = f"SAP: {e}"
                next_send = now + float(SAP_INTERVAL_SEC)
            time.sleep(0.2)

    def debug(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "ndi_source_name": self.ndi_source_name,
            "aes67_name": self.aes67_name,
            "mcast": f"{self.mcast_ip}:{self.mcast_port}",
            "audio_frames": self.audio_frames,
            "rtp_packets": getattr(self._rtp, "packets_sent", self.rtp_packets),
            "rtp_last_error": getattr(self._rtp, "last_send_error", ""),
            "rtp_iface": getattr(self._rtp, "mcast_iface", None),
            "error": self._error,
        }
