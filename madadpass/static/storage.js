const db = new Promise((resolve, reject) => {
  const request = indexedDB.open('madadpass-drafts', 1);
  request.onupgradeneeded = () => request.result.createObjectStore('entries');
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});

async function access(mode, operation) {
  const connection = await db;
  return new Promise((resolve, reject) => {
    const transaction = connection.transaction('entries', mode);
    const request = operation(transaction.objectStore('entries'));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export const drafts = {
  get: key => access('readonly', entries => entries.get(key)),
  put: (key, value) => access('readwrite', entries => entries.put(value, key)),
  remove: key => access('readwrite', entries => entries.delete(key)),
  all: () => access('readonly', entries => entries.getAll()),
};
