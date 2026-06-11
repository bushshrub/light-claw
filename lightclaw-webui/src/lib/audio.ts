// Audio recording service for light-claw WebUI

export class AudioRecorder {
    mediaRecorder: MediaRecorder | null = null;
    stream: MediaStream | null = null;
    chunks: Blob[] = [];
    isRecording = false;
    recordingTime = 0;
    timer: ReturnType<typeof setTimeout> | null = null;
    onRecordingChange?: (isRecording: boolean) => void;
    onTimeUpdate?: (time: number) => void;
    onAudioRecorded?: (blob: Blob) => void;

    async startRecording(): Promise<boolean> {
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.mediaRecorder = new MediaRecorder(this.stream, {
                mimeType: 'audio/webm;codecs=opus'
            });
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.chunks.push(event.data);
                }
            };
            
            this.mediaRecorder.onstop = () => {
                this.isRecording = false;
                this.onRecordingChange?.(false);
                
                if (this.chunks.length > 0) {
                    const blob = new Blob(this.chunks, { type: 'audio/webm' });
                    this.onAudioRecorded?.(blob);
                }
                
                this.cleanup();
            };
            
            this.mediaRecorder.start();
            this.isRecording = true;
            this.onRecordingChange?.(true);
            this.startTimer();
            
            return true;
            
        } catch (error) {
            console.error('Error starting recording:', error);
            this.cleanup();
            return false;
        }
    }

    stopRecording(): void {
        if (this.mediaRecorder && this.isRecording) {
            this.mediaRecorder.stop();
            this.stopTimer();
        }
    }

    startTimer(): void {
        this.timer = setInterval(() => {
            this.recordingTime++;
            this.onTimeUpdate?.(this.recordingTime);
        }, 1000);
    }

    stopTimer(): void {
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = null;
        }
    }

    cleanup(): void {
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
        
        this.chunks = [];
        this.isRecording = false;
    }

    isCurrentlyRecording(): boolean {
        return this.isRecording;
    }

    getRecordingTime(): number {
        return this.recordingTime;
    }

    formatTime(seconds: number): string {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    destroy(): void {
        this.stopRecording();
        this.cleanup();
    }
}

// Utility functions for audio processing
export async function convertToWav(blob: Blob): Promise<Blob> {
    // This would require additional audio processing libraries
    // For now, return the original blob
    return blob;
}

export function getSupportedAudioTypes(): string[] {
    return ['audio/wav', 'audio/webm', 'audio/mp3', 'audio/mpeg', 'audio/x-m4a', 'audio/flac'];
}

export function isAudioFile(file: File): boolean {
    return getSupportedAudioTypes().includes(file.type);
}
