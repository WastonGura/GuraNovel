import '@testing-library/jest-dom/vitest'

const storageData = new WeakMap<object, Record<string, string>>()

if (typeof window !== 'undefined' && typeof Storage !== 'undefined') {
  Storage.prototype.getItem = function (key: string) {
    const data = storageData.get(this) || {}
    return data[key] ?? null
  }
  Storage.prototype.setItem = function (key: string, value: string) {
    let data = storageData.get(this)
    if (!data) {
      data = {}
      storageData.set(this, data)
    }
    data[key] = String(value)
  }
  Storage.prototype.removeItem = function (key: string) {
    const data = storageData.get(this)
    if (data) {
      delete data[key]
    }
  }
  Storage.prototype.clear = function () {
    storageData.set(this, {})
  }
  Storage.prototype.key = function (index: number) {
    const data = storageData.get(this) || {}
    return Object.keys(data)[index] ?? null
  }
  try {
    Object.defineProperty(Storage.prototype, 'length', {
      get: function () {
        const data = storageData.get(this) || {}
        return Object.keys(data).length
      },
      configurable: true,
    })
  } catch {
    // Ignore environment definition conflicts
  }

  const mockLocalStorage = Object.create(Storage.prototype)
  storageData.set(mockLocalStorage, {})
  const mockSessionStorage = Object.create(Storage.prototype)
  storageData.set(mockSessionStorage, {})

  for (const target of [globalThis, window]) {
    try {
      Object.defineProperty(target, 'localStorage', {
        value: mockLocalStorage,
        writable: true,
        configurable: true,
      })
      Object.defineProperty(target, 'sessionStorage', {
        value: mockSessionStorage,
        writable: true,
        configurable: true,
      })
    } catch {
      // Ignore environment definition conflicts
    }
  }
}





