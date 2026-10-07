import { useCallback, useEffect, useState } from 'react';
import {
  archiveContact,
  createContact,
  deleteContact,
  deleteContactMemoryEntry,
  listContactMemory,
  listContacts,
  restoreContact,
  saveContactMemoryEntry,
  updateContact,
} from '../api/contacts';
import type {
  ContactMemoryRecord,
  ContactRecord,
  CreateContactInput,
  SaveContactMemoryInput,
  UpdateContactInput,
} from '../api/types/contact';

export function useContacts() {
  const [contacts, setContacts] = useState<ContactRecord[]>([]);
  const [selectedContact, setSelectedContact] = useState<ContactRecord | null>(
    null
  );
  const [memoryEntries, setMemoryEntries] = useState<ContactMemoryRecord[]>([]);
  const [search, setSearch] = useState('');
  const [lifecycleFilter, setLifecycleFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listContacts({
        lifecycle: lifecycleFilter !== 'all' ? lifecycleFilter : undefined,
        search: search || undefined,
      });
      setContacts(rows);
      if (selectedContact) {
        const refreshed = rows.find((c) => c.id === selectedContact.id) || null;
        setSelectedContact(refreshed);
      } else if (rows.length > 0) {
        setSelectedContact(rows[0]);
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to load contacts');
    } finally {
      setLoading(false);
    }
  }, [lifecycleFilter, search, selectedContact]);

  useEffect(() => {
    reload();
  }, [lifecycleFilter, search]);

  const loadMemory = useCallback(async (contactId: string) => {
    try {
      const entries = await listContactMemory(contactId);
      setMemoryEntries(entries);
      return entries;
    } catch {
      setMemoryEntries([]);
      return [];
    }
  }, []);

  useEffect(() => {
    if (selectedContact?.id) {
      loadMemory(selectedContact.id);
    } else {
      setMemoryEntries([]);
    }
  }, [selectedContact?.id, loadMemory]);

  const upsertContact = useCallback(
    async (input: CreateContactInput) => {
      const created = await createContact(input);
      await reload();
      setSelectedContact(created);
      return created;
    },
    [reload]
  );

  const patchContact = useCallback(
    async (contactId: string, input: UpdateContactInput) => {
      const updated = await updateContact(contactId, input);
      await reload();
      setSelectedContact(updated);
      return updated;
    },
    [reload]
  );

  const archive = useCallback(
    async (contactId: string) => {
      const updated = await archiveContact(contactId);
      await reload();
      return updated;
    },
    [reload]
  );

  const restore = useCallback(
    async (contactId: string) => {
      const updated = await restoreContact(contactId);
      await reload();
      return updated;
    },
    [reload]
  );

  const remove = useCallback(
    async (contactId: string) => {
      await deleteContact(contactId);
      setSelectedContact(null);
      await reload();
    },
    [reload]
  );

  const saveMemory = useCallback(
    async (contactId: string, key: string, input: SaveContactMemoryInput) => {
      const saved = await saveContactMemoryEntry(contactId, key, input);
      await loadMemory(contactId);
      return saved;
    },
    [loadMemory]
  );

  const deleteMemory = useCallback(
    async (contactId: string, key: string) => {
      await deleteContactMemoryEntry(contactId, key);
      await loadMemory(contactId);
    },
    [loadMemory]
  );

  return {
    contacts,
    selectedContact,
    setSelectedContact,
    memoryEntries,
    search,
    setSearch,
    lifecycleFilter,
    setLifecycleFilter,
    loading,
    error,
    reload,
    loadMemory,
    upsertContact,
    patchContact,
    archive,
    restore,
    remove,
    saveMemory,
    deleteMemory,
  };
}
