import { useEffect, useState } from 'react';

const API = '';
type State<T> = {key: string; data: T | null; error: string | null; loading: boolean};

/** No retries; abort, deadline and identity guard also handle fetch ignoring abort. */
export function useReadRequest<T>(path: string | null, body?: string) {
  const [attempt, retry] = useState(0);
  const key = JSON.stringify([path, body, attempt]);
  const [state, setState] = useState<State<T>>({key: '', data: null, error: null, loading: false});
  useEffect(() => {
    if (!path) return;
    let active = true;
    const controller = new AbortController();
    const finish = (data: T | null, error: string | null) => {
      if (active) setState({key, data, error, loading: false});
    };
    const timer = setTimeout(() => {
      finish(null, '请求超时，请重试'); active = false; controller.abort();
    }, 15000);
    Promise.resolve().then(async () => {
      if (!active) return;
      try {
        const response = await fetch(API + path, {signal: controller.signal, cache: 'no-store',
          method: body ? 'POST' : 'GET', headers: body ? {'Content-Type': 'application/json'} : undefined, body});
        if (!response.ok) throw new Error(`读取失败（${response.status}）`);
        finish(await response.json() as T, null);
      } catch (error) {
        finish(null, error instanceof Error ? error.message : '读取失败');
      } finally { clearTimeout(timer); }
    });
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [key, path, body]);
  const current = state.key === key ? state : {key, data: null, error: null, loading: !!path};
  return {data: current.data, error: current.error, loading: current.loading, retry: () => retry(value => value + 1)};
}
