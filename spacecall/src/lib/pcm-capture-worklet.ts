class PcmCaptureProcessor extends AudioWorkletProcessor {
  process(inputs: Float32Array[][]) {
    const ch0 = inputs[0]?.[0];
    if (ch0 && ch0.length) {
      const copy = new Float32Array(ch0.length);
      copy.set(ch0);
      this.port.postMessage(copy, [copy.buffer]);
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor);