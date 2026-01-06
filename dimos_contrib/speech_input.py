#!/usr/bin/env python3
"""Voice input using faster-whisper for transcription and sending to /human_input."""

import signal
import sys
import time

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]
from faster_whisper import WhisperModel

from dimos.core import pLCMTransport

# Configuration
MODEL_SIZE = "small"
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.1  # seconds
SILENCE_THRESHOLD = 0.01  # RMS amplitude threshold for silence
SILENCE_DURATION = 1.5  # seconds of silence to detect end of sentence
MIN_RECORDING_DURATION = 0.5  # minimum recording duration in seconds

# Run on GPU with FP16, or CPU with int8
# model = WhisperModel(MODEL_SIZE, device="cuda", compute_type="float16")
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

# Create transport for sending to /human_input
transport = pLCMTransport("/human_input")

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully."""
    global shutdown_requested
    print("\n\nShutdown requested...")
    shutdown_requested = True


def get_audio_level(audio_chunk: np.ndarray) -> float:
    """Calculate RMS (Root Mean Square) audio level."""
    return np.sqrt(np.mean(audio_chunk**2))


def record_until_silence() -> np.ndarray | None:
    """
    Record audio from microphone until silence is detected.
    Returns the recorded audio as a numpy array, or None if no audio was recorded.
    """
    global shutdown_requested
    
    audio_buffer = []
    is_recording = False
    silence_chunks = 0
    silence_threshold_chunks = int(SILENCE_DURATION / CHUNK_DURATION)
    
    print("Listening... (speak now)")
    
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype=np.float32) as stream:
            chunk_samples = int(SAMPLE_RATE * CHUNK_DURATION)
            
            while True:
                # Check for shutdown request
                if shutdown_requested:
                    print("\nShutdown requested during recording")
                    return None
                
                chunk, overflowed = stream.read(chunk_samples)
                if overflowed:
                    print("Warning: Audio buffer overflowed")
                
                # Calculate audio level
                level = get_audio_level(chunk)
                
                if not is_recording:
                    # Wait for speech to start
                    if level > SILENCE_THRESHOLD:
                        is_recording = True
                        audio_buffer.append(chunk.flatten())
                        print("Recording...", end="", flush=True)
                else:
                    # Recording in progress
                    audio_buffer.append(chunk.flatten())
                    print(".", end="", flush=True)
                    
                    # Check for silence
                    if level <= SILENCE_THRESHOLD:
                        silence_chunks += 1
                        if silence_chunks >= silence_threshold_chunks:
                            # Silence detected - stop recording
                            print("\nSilence detected, processing...")
                            break
                    else:
                        silence_chunks = 0
                        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        shutdown_requested = True
        return None
    except Exception as e:
        print(f"\nError during recording: {e}")
        return None
    
    if not audio_buffer:
        return None
    
    # Combine all chunks into a single array and ensure it's 1D
    recorded_audio = np.concatenate(audio_buffer)
    if recorded_audio.ndim > 1:
        recorded_audio = recorded_audio.flatten()
    
    # Check minimum duration
    duration = len(recorded_audio) / SAMPLE_RATE
    if duration < MIN_RECORDING_DURATION:
        print(f"Recording too short ({duration:.2f}s), ignoring")
        return None
    
    print(f"Recorded {duration:.2f} seconds of audio")
    return recorded_audio


def transcribe_audio(audio: np.ndarray) -> str:
    """Transcribe audio using faster-whisper."""
    try:
        # faster-whisper expects float32 audio in range [-1, 1]
        # Our audio should already be in this format from sounddevice
        # Ensure it's a 1D array
        if audio.ndim > 1:
            audio = audio.flatten()
        
        # Transcribe with VAD filter enabled for better accuracy
        segments, info = model.transcribe(
            audio,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        
        # Combine all segments into a single text
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())
        
        text = " ".join(text_parts).strip()
        return text
    except Exception as e:
        print(f"Error during transcription: {e}")
        import traceback
        traceback.print_exc()
        return ""


def main() -> None:
    """Main loop: continuously listen for speech, transcribe, and send to /human_input."""
    global shutdown_requested
    
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    print("Voice input initialized")
    print(f"Model: {MODEL_SIZE}")
    print(f"Sample rate: {SAMPLE_RATE} Hz")
    print(f"Silence threshold: {SILENCE_THRESHOLD}")
    print(f"Silence duration: {SILENCE_DURATION} seconds")
    print("\nPress Ctrl+C to exit\n")
    
    try:
        while not shutdown_requested:
            # Record audio until silence is detected
            audio = record_until_silence()
            
            # Check again after recording (might have been interrupted)
            if shutdown_requested:
                break
            
            if audio is None:
                continue
            
            # Transcribe the audio
            print("Transcribing...")
            text = transcribe_audio(audio)
            
            if not text:
                print("No transcription received")
                continue
            
            # Print the transcribed text
            print(f"Transcribed: {text}")
            
            # Send to /human_input
            try:
                transport.publish(text)
                print(f"Sent to /human_input: {text}\n")
            except Exception as e:
                print(f"Error sending to /human_input: {e}\n")
            
            # Small delay before listening again
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        shutdown_requested = True
    except Exception as e:
        print(f"\nError in main loop: {e}")
    finally:
        print("\nExiting gracefully...")
        sys.exit(0)


if __name__ == "__main__":
    main()
