export async function decodeJpeg(jpeg: Uint8Array): Promise<string> {
  const url = URL.createObjectURL(new Blob([new Uint8Array(jpeg)], { type: 'image/jpeg' }));
  try {
    await new Promise<void>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('Invalid frame'));
      image.src = url;
    });
    return url;
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}
