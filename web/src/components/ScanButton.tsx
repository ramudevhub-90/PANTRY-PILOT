import { useRef } from 'react';
import { Camera } from 'lucide-react';
import { useAssistant } from '../agent/useAssistant';
import { downscaleImage } from '../lib/media';

const MAX_PHOTOS = 4;

interface Props {
  variant?: 'button' | 'icon' | 'hero';
  disabled?: boolean;
  /** Open the phone's camera straight away instead of the photo picker. */
  camera?: boolean;
  onPicked?: () => void;
}

export function ScanButton({ variant = 'button', disabled, camera, onPicked }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { ask } = useAssistant();

  async function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = [...(e.target.files ?? [])].slice(0, MAX_PHOTOS);
    e.target.value = '';
    if (!files.length) return;
    onPicked?.();
    const images = await Promise.all(files.map((f) => downscaleImage(f)));
    void ask(images.length > 1 ? `Scan these ${images.length} grocery photos` : 'Scan my groceries', { images });
  }

  const open = () => inputRef.current?.click();
  const label = 'Scan grocery photos';

  return (
    <>
      <input ref={inputRef} type="file" accept="image/*" multiple={!camera} capture={camera ? 'environment' : undefined} hidden onChange={onChange} />
      {variant === 'icon' ? (
        <button type="button" className="icon-btn" onClick={open} disabled={disabled} aria-label={label} title={label}>
          <Camera size={18} />
        </button>
      ) : variant === 'hero' ? (
        <button type="button" className="btn" onClick={open} disabled={disabled}>
          <Camera size={17} />
          Take a photo
        </button>
      ) : (
        <button type="button" className="btn btn--secondary btn--sm" onClick={open} disabled={disabled}>
          <Camera size={15} />
          Scan groceries
        </button>
      )}
    </>
  );
}
