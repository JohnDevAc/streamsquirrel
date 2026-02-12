from dataclasses import dataclass

# ------------------------------
# AES67 audio format
# ------------------------------
AES67_SAMPLE_RATE = 48000
AES67_CHANNELS = 2
AES67_BIT_DEPTH = 24
AES67_PAYLOAD_TYPE = 96

# Packetization: default 4ms @ 48kHz => 192 samples/channel.
# (1ms/48 samples is valid but is much more sensitive to CPU jitter and network micro-bursts.)
AES67_SAMPLES_PER_PACKET = 48

# ------------------------------
# SAP (AES67 discovery)
# ------------------------------
# Dante Controller listens for SAP on 239.255.255.255:9875
SAP_GROUP = "239.255.255.255"
SAP_PORT = 9875

# Announce more frequently to help receivers discover quickly and to reduce stale-cache issues after unclean reboot.
SAP_INTERVAL_SEC = 1.0

# Optionally force the SAP "originating source" IPv4 (recommended to pin to the Dante/AES67 NIC IP).
# If not set, we auto-pick the first non-loopback interface.
# Example:
#   export SAP_SRC_IP=10.0.0.50
SAP_SRC_IP_ENV = "SAP_SRC_IP"

# Optional PTP identity (used only in SDP; does not discipline the sender clock).
# Example:
#   export PTP_GMID=00-11-22-33-44-55-66-77
#   export PTP_DOMAIN=0
PTP_GMID_ENV = "PTP_GMID"
PTP_DOMAIN_ENV = "PTP_DOMAIN"

DEFAULT_SLOTS = [
    ("239.69.0.10", 5004),
    ("239.69.0.11", 5004),
    ("239.69.0.12", 5004),
    ("239.69.0.13", 5004),
]

SDP_ORIGIN_USER = "SSQ"
SDP_SESSION_NAME_PREFIX = "AES67"

