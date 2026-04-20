import wave, struct, math
import sys
import os

def analyze_wav(path, label):
    print(f"\nChecking file: {path}")
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    try:
        with wave.open(path, 'rb') as w:
            nch = w.getnchannels()
            sw = w.getsampwidth()
            fr = w.getframerate()
            nf = w.getnframes()
            raw = w.readframes(nf)
    except Exception as e:
        print(f"Error opening {path}: {e}")
        return

    fmt = {1: 'b', 2: 'h', 4: 'i'}.get(sw)
    if not fmt:
        print(f"Unsupported sample width: {sw}")
        return
        
    samples = struct.unpack(f"<{nf * nch}{fmt}", raw)
    # Use first channel only
    mono = samples[::nch]
    scale = float(2**(sw*8-1))
    normalized = [s / scale for s in mono]
    
    duration_ms = nf / fr * 1000
    print(f"\n=== {label} ===")
    print(f"  Channels={nch}, SampleWidth={sw}B, Rate={fr}Hz, Frames={nf}, Duration={duration_ms:.1f}ms")
    
    # Peak overall
    peak = max(abs(s) for s in normalized)
    print(f"  Peak amplitude: {peak:.4f}")
    
    # Listen to start/end
    print(f"\n  First 50ms envelope (every 10ms):")
    for ms in range(0, 51, 10):
        start = int(fr * ms / 1000)
        end = min(start + int(fr * 0.005), len(normalized))
        if start >= len(normalized): break
        chunk = normalized[start:end]
        rms = math.sqrt(sum(x*x for x in chunk) / len(chunk)) if chunk else 0
        print(f"    t={ms:3d}ms rms={rms:.4f}")
    
    # Detect likely loop point: find total duration / 4
    loop_len = nf // 4
    print(f"\n  Estimated loop length: {loop_len} frames = {loop_len/fr*1000:.1f}ms")
    
    # Check sample value right at boundary for discontinuity
    print(f"\n  Sample discontinuities at loop boundaries:")
    for loop_num in range(1, 4):
        boundary = loop_num * loop_len
        if boundary < 1 or boundary >= len(normalized): continue
        before = normalized[boundary - 1]
        at = normalized[boundary]
        jump = abs(at - before)
        print(f"    Boundary {loop_num}: sample[{boundary-1}]={before:.4f}  sample[{boundary}]={at:.4f}  jump={jump:.4f}")

base = r"C:\Users\brian\Documents\GitHub\JingleAllTheDay"
analyze_wav(os.path.join(base, "Dean Scream - 4 Perfect Loops.wav"), "Dean Scream - 4 Perfect Loops (Reference)")
analyze_wav(os.path.join(base, "Dean Scream - 4 Loops From App.wav"), "Dean Scream - 4 Loops From App (Loopback Recording)")
