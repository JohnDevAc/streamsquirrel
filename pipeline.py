import threading
import time
import random
from typing import Optional

from ndi_backend import NDIReceiver, NDIBackendError
from aes67_rtp import RTPAES67Sender, RTPMonitorSender
from sap import SAPAnnouncer, build_sdp
from config import AES67_SAMPLES_PER_PACKET, SAP_INTERVAL_SEC, MONITOR_ENABLE, MONITOR_PAYLOAD_TYPE, MONITOR_PORT_OFFSET

class SlotPipeline:
    def __init__(self, slot_id: int, ndi_source_name: str, aes67_name: str, mcast_ip: str, mcast_port: int):
        self.slot_id = slot_id
        self.ndi_source_name = ndi_source_name
        self.aes67_name = aes67_name
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
        self._sap = SAPAnnouncer(build_sdp(aes67_name, mcast_ip, mcast_port, payload_type=96, codec="L24"))

        self._rtp_mon = None
        self._sap_mon = None
        self._mon_port = mcast_port + MONITOR_PORT_OFFSET
        if MONITOR_ENABLE:
            self._rtp_mon = RTPMonitorSender(mcast_ip, self._mon_port, ssrc=random.randint(1, 0xFFFFFFFF), payload_type=MONITOR_PAYLOAD_TYPE)
            self._sap_mon = SAPAnnouncer(build_sdp(aes67_name + " (Monitor L16)", mcast_ip, self._mon_port, payload_type=MONITOR_PAYLOAD_TYPE, codec="L16"))

    def start(self):
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
        if self._rtp_mon:
            try: self._rtp_mon.close()
            except Exception: pass
        if self._sap_mon:
            try: self._sap_mon.close()
            except Exception: pass

    def _run(self):
        try:
            for (pcm_i32_le, samples_per_channel) in self._receiver.read_audio():
                if self._stop.is_set():
                    break

                if samples_per_channel != AES67_SAMPLES_PER_PACKET:
                    continue

                self.audio_frames += 1
                self._rtp.send_int32le_frame(pcm_i32_le, samples_per_channel)
                self.rtp_packets = getattr(self._rtp, 'packets_sent', self.rtp_packets)

                if self._rtp_mon:
                    self._rtp_mon.send_int32le_frame(pcm_i32_le, samples_per_channel)
                    self.rtp_packets_mon = getattr(self._rtp_mon, 'packets_sent', self.rtp_packets_mon)


        except Exception as e:
            self._error = str(e)
            self.last_exception = str(e)
            print(f"[PIPELINE] slot {self.slot_id} error: {e}")


    def _sap_loop(self):
        """Periodically announce SDP via SAP for the lifetime of the pipeline."""
        # Send immediately so receivers discover the stream quickly.
        next_send = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now >= next_send:
                try:
                    self._sap.send_once()
                    if self._sap_mon:
                        self._sap_mon.send_once()
                except Exception as e:
                    # Don't kill the pipeline due to transient SAP send errors.
                    self.last_exception = f"SAP: {e}"
                next_send = now + float(SAP_INTERVAL_SEC)
            # Short sleep keeps timing decent without busy-waiting.
            time.sleep(0.2)


    def debug(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "ndi_source_name": self.ndi_source_name,
            "aes67_name": self.aes67_name,
            "mcast": f"{self.mcast_ip}:{self.mcast_port}",
            "monitor_port": getattr(self, "_mon_port", None),
            "audio_frames": self.audio_frames,
            "rtp_packets": getattr(self._rtp, "packets_sent", self.rtp_packets),
            "rtp_last_error": getattr(self._rtp, "last_send_error", ""),
            "rtp_iface": getattr(self._rtp, "mcast_iface", None),
            "rtp_mon_packets": getattr(self._rtp_mon, "packets_sent", 0) if self._rtp_mon else 0,
            "rtp_mon_last_error": getattr(self._rtp_mon, "last_send_error", "") if self._rtp_mon else "",
            "rtp_mon_iface": getattr(self._rtp_mon, "mcast_iface", None) if self._rtp_mon else None,
            "error": self._error,
        }
