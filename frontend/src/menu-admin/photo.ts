/**
 * Getting a chosen photo ready to upload: phone photos are several megabytes
 * and far larger than a menu needs, so they are shrunk in the browser first.
 */

const LONGEST_SIDE = 1200
const QUALITY = 0.85
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024
const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp']

/** For the file picker's `accept`. */
export const ACCEPT = ACCEPTED.join(',')

export class PhotoProblem extends Error {}

/**
 * A JPEG no larger than 1200 px on its longest side. If the browser cannot
 * redraw the image, the original is used, as long as the backend takes it.
 */
export async function preparePhoto(file: File): Promise<Blob> {
  if (!file.type.startsWith('image/')) {
    throw new PhotoProblem('Choose an image file: JPEG, PNG or WebP.')
  }
  try {
    return await shrink(file)
  } catch {
    if (!ACCEPTED.includes(file.type)) {
      throw new PhotoProblem('This kind of image cannot be used. Choose a JPEG, PNG or WebP.')
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      throw new PhotoProblem('This photo is larger than 5 MB. Choose a smaller one.')
    }
    return file
  }
}

async function shrink(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, LONGEST_SIDE / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  const context = canvas.getContext('2d')
  if (!context) throw new Error('No 2D canvas')
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', QUALITY))
  if (!blob) throw new Error('Could not encode the photo')
  return blob
}
