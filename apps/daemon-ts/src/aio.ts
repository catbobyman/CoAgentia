/**
 * asyncio 对应物（迁移共享底座）：Lock / Event / Queue / sleep / withTimeout。
 *
 * py 侧 asyncio.Lock（git 单车道）、asyncio.Event（pong/flush/connected）、asyncio.Queue
 * （测试传输）在 TS 侧的统一实现——各模块禁止各自发明（纪律 7 同族）。
 */

/** 互斥锁（对等 asyncio.Lock）：runExclusive 保证释放。 */
export class Lock {
  private tail: Promise<void> = Promise.resolve();

  async runExclusive<T>(fn: () => Promise<T>): Promise<T> {
    const prev = this.tail;
    let release!: () => void;
    this.tail = new Promise<void>((r) => {
      release = r;
    });
    await prev;
    try {
      return await fn();
    } finally {
      release();
    }
  }
}

/** 事件旗标（对等 asyncio.Event）：set/clear/wait。 */
export class AsyncEvent {
  private flag = false;
  private waiters: Array<() => void> = [];

  set(): void {
    this.flag = true;
    const ws = this.waiters;
    this.waiters = [];
    for (const w of ws) w();
  }

  clear(): void {
    this.flag = false;
  }

  isSet(): boolean {
    return this.flag;
  }

  async wait(): Promise<void> {
    if (this.flag) return;
    await new Promise<void>((r) => this.waiters.push(r));
  }
}

/** 无界队列（对等 asyncio.Queue）：put 即时、get 挂起。 */
export class AsyncQueue<T> {
  private items: T[] = [];
  private waiters: Array<(v: T) => void> = [];

  put(item: T): void {
    const w = this.waiters.shift();
    if (w !== undefined) w(item);
    else this.items.push(item);
  }

  async get(): Promise<T> {
    const item = this.items.shift();
    if (item !== undefined) return item;
    return new Promise<T>((r) => this.waiters.push(r));
  }

  get size(): number {
    return this.items.length;
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** 超时错误（对等 asyncio.TimeoutError 语义位）。 */
export class TimeoutError extends Error {}

/** 对等 asyncio.wait_for：超时抛 TimeoutError（不取消底层 Promise——TS 无取消注入，调用方自管收尾）。 */
export async function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      p,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new TimeoutError(`timeout after ${ms}ms`)), ms);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
