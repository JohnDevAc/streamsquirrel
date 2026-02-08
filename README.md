# NDI → AES67 Bridge (Raspberry Pi 5)

This project receives up to 4 separate NDI streams, extracts stereo audio, and multicasts each as an AES67 RTP stream.
It also announces the AES67 streams via SAP/SDP for discovery by Dante AES67 receivers.

## Features
- Web UI: discover NDI sources on the network
- Map up to 4 NDI sources to 4 AES67 output slots
- AES67 stream names default to the NDI name and are editable **before** starting
- Configuration is locked while running
- Status panel: Live / Offline
- AES67: RTP L24/48kHz/2ch, 1ms packets (48 samples/channel)
- SAP: periodic announcements to 224.2.127.254:9875

## Prerequisites
1. Raspberry Pi 5 (64-bit Raspberry Pi OS recommended)
2. NDI SDK for Linux installed (NDI Runtime + headers)
3. NDI library available on the system:
   - Typically `libndi.so` is installed with the NDI runtime.
   - Ensure the loader can find it: `ldconfig -p | grep ndi` or set `LD_LIBRARY_PATH`.

## Install

Easy:

Golden Image
https://www.johnlightfoot.biz/streamsquirrel.rar

GOLDEN IMAGE WILL LIKELY BE BEHIND THE CURRENT CODEBASE, YOU CAN REPLACE PROJECT FILES ON THE PI FROM THIS REPO ONCE YOU HAVE THE IMAGE RUNNING TO UPDATE.

NOTE THE IMAGE DISABLES WLAN0 ON BOOT THIS IS ETH0 ONLY.

Download, unrar, image to sd card and pop it in your PI5

SSH: admin:admin

------------------------------------------------------------------------------------

For those with time and curiosity..

if using only source files on this repo, be aware you will need the NDI SDK + prereq listed in requirements.txt , setup of systemd service for boot and persistent permissions for user: "steamsquirrel" as root with no password for sudo. There is probably more!

INSTALL NDI SDK FIRST

Download the NDI SDK from 
https://ndi.video/for-developers/ndi-sdk/

Your package expects libndi.so to exist at:

/usr/local/lib/libndi.so

So after you install the NDI runtime (from the NDI SDK), make it readable and registered:

### Copy libndi.so for aarch64 (Pi 4/5 64-bit) into /usr/local/lib
```bash
sudo cp -f "/path/to/NDI SDK for Linux/lib/aarch64-rpi4-linux-gnueabi/libndi.so.6.2.1" /usr/local/lib/libndi.so.6.2.1
sudo ln -sf /usr/local/lib/libndi.so.6.2.1 /usr/local/lib/libndi.so.6
sudo ln -sf /usr/local/lib/libndi.so.6 /usr/local/lib/libndi.so

sudo chown root:root /usr/local/lib/libndi.so.6.2.1
sudo chmod 0644 /usr/local/lib/libndi.so.6.2.1
sudo ldconfig
```

## Notes / Limitations
- THE GOLDEN IMAGE WILL DISABLE YOUR WIFI ON BOOT THIS IS ETH0 ONLY
- This implementation expects NDI audio frames to be Float32 planar, which is typical for NDI SDK audio v2.
- The bridge currently supports **48kHz, 2-channel** AES67 output.
  - If the NDI source is not 48kHz or not 2ch, the pipeline will refuse to start for that slot.
- For production, consider:
  - Adding resampling & downmix/upmix
  - Tighter RTP pacing with a dedicated clock
  - PTP (ptp4l/phc2sys) on the network, as AES67 expects PTP

## Troubleshooting
- If no NDI sources appear:
  - Confirm NDI tools on the same LAN can see sources
  - Verify multicast is not blocked
  - Check `libndi.so` is loadable (see above)


## Fix note
If Start appears to do nothing, it is usually because the receiver failed to connect. This build keeps the NDI source strings alive (avoids dangling pointers) and surfaces start errors in the UI status pill.

## SDP download
Each active slot shows a **Download SDP** button in the UI. The button is enabled only when that slot is Live/outputting AES67.

## VLC monitoring
VLC often does not decode AES67 `L24` RTP. This build also outputs an optional monitor stream per active slot:
- Codec: L16/48000/2
- Port: base_port + 2 (e.g. 5006 when AES67 is 5004)

Use **Download SDP (Monitor L16)** in the UI and open that SDP in VLC.

## PTP attributes in SDP
If you want Dante-style PTP SDP attributes, set:
- `PTP_GMID` e.g. `00-11-22-33-44-55-66-77`
- optional `PTP_DOMAIN` e.g. `0`

If unset, the SDP omits `a=ts-refclk` but still includes `a=mediaclk:direct=0`.

## Diagnostics
You can verify if packets are being sent:
```bash
sudo tcpdump -n -i any udp and dst host 239.69.0.1 and dst port 5004
sudo tcpdump -n -i any udp and dst host 239.69.0.1 and dst port 5006
```

This build also exposes per-slot debug stats:
```bash
curl -s http://127.0.0.1:8080/api/debug/slot/1 ; echo
```

## Multicast interface selection
By default, the app auto-picks a non-loopback interface for multicast.
You can force the outgoing interface:
```bash
export MCAST_IFACE=eth0
```
