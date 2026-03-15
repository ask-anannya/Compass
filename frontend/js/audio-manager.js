const JITTER_MS = 180; // hold playback until this many ms are buffered

class AudioManager {
  constructor(sessionId, onTranscript, onStatus, onTurnEnd, onPlanRequested, onDiagramRequested) {
    this.sessionId       = sessionId;
    this.onTranscript    = onTranscript;
    this.onStatus        = onStatus;
    this.onTurnEnd          = onTurnEnd          || (() => {});
    this.onPlanRequested    = onPlanRequested    || (() => {});
    this.onDiagramRequested = onDiagramRequested || (() => {});

    this.audioCtx24   = new AudioContext({ sampleRate: 24000 });
    this.audioCtx16   = null;
    this.audioQueue    = [];
    this.activeSources = [];   // scheduled BufferSourceNodes so we can stop them
    this.bufferedMs    = 0;    // ms of audio waiting in queue
    this.nextStartTime = 0;    // Web Audio clock time for next chunk to start
    this.micStream    = null;
    this.micProcessor = null;
    this.ws           = null;
    this.stopped      = false;
  }

  async start() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/brief/audio/${this.sessionId}`;
    console.log('[AudioManager] Connecting to:', url);
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('[AudioManager] WebSocket opened (101 received)');
      // Do NOT start mic here — wait for 'ready' event after initial briefing
    };

    this.ws.onmessage = async (e) => {
      const msg = JSON.parse(e.data);
      if (msg.event === 'audio')       this._enqueueAudio(msg.data);
      if (msg.event === 'interrupted') { this._flushAudio(); this.onTurnEnd(); }
      if (msg.event === 'transcript')  this.onTranscript(msg.text);
      if (msg.event === 'ready') {
        // Initial briefing complete — safe to activate mic for Q&A
        try { this.onTurnEnd(); } catch (_) {}
        await this._startMic();
      }
      if (msg.event === 'plan_requested')    this.onPlanRequested(msg.text);
      if (msg.event === 'diagram_requested') this.onDiagramRequested();
      if (msg.event === 'error')             this.onStatus(`Error: ${msg.msg}`);
    };

    this.ws.onclose = () => {
      if (!this.stopped) this.onStatus('Session ended');
    };

    this.ws.onerror = (e) => { console.error('[AudioManager] WebSocket error:', e); this.onStatus('WebSocket error — check console'); };
  }

  stop() {
    this.stopped = true;
    if (this.ws) {
      try { this.ws.send(JSON.stringify({ type: 'stop' })); } catch (_) {}
      this.ws.close();
    }
    this._stopMic();
    this._flushAudio();
    this.onStatus('Stopped');
  }

  // ── Mic capture ───────────────────────────────────────────────────────────

  async _startMic() {
    if (this.micStream) return;   // already running — ready fires after every turn, not just the first
    this.micStream  = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    this.audioCtx16 = new AudioContext({ sampleRate: 16000 });
    const source    = this.audioCtx16.createMediaStreamSource(this.micStream);
    this.micProcessor = this.audioCtx16.createScriptProcessor(4096, 1, 1);

    this.micProcessor.onaudioprocess = (e) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const floats = e.inputBuffer.getChannelData(0);
      const pcm16  = new Int16Array(floats.length);
      for (let i = 0; i < floats.length; i++)
        pcm16[i] = Math.max(-32768, Math.min(32767, floats[i] * 32768));

      const bytes = new Uint8Array(pcm16.buffer);
      let binary  = '';
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      this.ws.send(JSON.stringify({ type: 'audio', data: btoa(binary) }));
    };

    source.connect(this.micProcessor);
    this.micProcessor.connect(this.audioCtx16.destination);
  }

  _stopMic() {
    if (this.micProcessor) { this.micProcessor.disconnect(); this.micProcessor = null; }
    if (this.audioCtx16)   { this.audioCtx16.close(); this.audioCtx16 = null; }
    if (this.micStream)    { this.micStream.getTracks().forEach(t => t.stop()); this.micStream = null; }
  }

  // ── Audio playback ────────────────────────────────────────────────────────

  _enqueueAudio(base64) {
    const raw    = atob(base64);
    const buf    = new Int16Array(raw.length / 2);
    for (let i = 0; i < buf.length; i++)
      buf[i] = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8);

    const floats = new Float32Array(buf.length);
    for (let i = 0; i < buf.length; i++) floats[i] = buf[i] / 32768;

    const audioBuf = this.audioCtx24.createBuffer(1, floats.length, 24000);
    audioBuf.copyToChannel(floats, 0);

    this.audioQueue.push(audioBuf);
    this.bufferedMs += audioBuf.duration * 1000;

    // Drain queue if jitter buffer is filled, or if we're already mid-playback
    const alreadyLive = this.nextStartTime > this.audioCtx24.currentTime;
    if (alreadyLive || this.bufferedMs >= JITTER_MS) this._drainQueue();
  }

  // Schedule all queued chunks back-to-back on the Web Audio timeline.
  // Using precise start times instead of onended chaining eliminates gaps.
  _drainQueue() {
    while (this.audioQueue.length) {
      const audioBuf = this.audioQueue.shift();
      this.bufferedMs = Math.max(0, this.bufferedMs - audioBuf.duration * 1000);

      const source = this.audioCtx24.createBufferSource();
      source.buffer = audioBuf;
      source.connect(this.audioCtx24.destination);

      const startAt = Math.max(this.audioCtx24.currentTime, this.nextStartTime);
      source.start(startAt);
      this.nextStartTime = startAt + audioBuf.duration;

      this.activeSources.push(source);
      source.onended = () => {
        const i = this.activeSources.indexOf(source);
        if (i !== -1) this.activeSources.splice(i, 1);
      };
    }
  }

  _flushAudio() {
    this.audioQueue    = [];
    this.bufferedMs    = 0;
    this.nextStartTime = 0; // reset so next session re-arms the jitter buffer
    for (const s of this.activeSources) { try { s.stop(); } catch (_) {} }
    this.activeSources = [];
  }
}
