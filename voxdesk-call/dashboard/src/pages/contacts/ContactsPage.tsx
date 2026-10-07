import React, { useState } from 'react';
import { useContacts } from '../../hooks/useContacts';

export function ContactsPage() {
  const {
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
    upsertContact,
    archive,
    restore,
    remove,
    saveMemory,
    deleteMemory,
  } = useContacts();

  const [phone, setPhone] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [company, setCompany] = useState('');
  const [memKey, setMemKey] = useState('');
  const [memValue, setMemValue] = useState('');
  const [memImportance, setMemImportance] = useState('80');
  const [notice, setNotice] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const handleCreateContact = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phone.trim()) return;
    setNotice(null);
    setFormError(null);
    try {
      const c = await upsertContact({
        phone: phone.trim(),
        name: name.trim(),
        email: email.trim() || null,
        company: company.trim() || null,
        source: 'manual',
      });
      setPhone('');
      setName('');
      setEmail('');
      setCompany('');
      setNotice(`Saved contact ${c.name || c.phone} (${c.phone}).`);
    } catch (err: any) {
      setFormError(err?.message || 'Failed to save contact');
    }
  };

  const handleSaveMemory = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedContact || !memKey.trim() || !memValue.trim()) return;
    setNotice(null);
    setFormError(null);
    try {
      const entry = await saveMemory(selectedContact.id, memKey.trim(), {
        value: memValue.trim(),
        value_type: 'string',
        source: 'manual',
        importance: parseInt(memImportance, 10) || 50,
        confidence: 1.0,
      });
      setMemKey('');
      setMemValue('');
      setNotice(`Saved durable memory key "${entry.key}".`);
    } catch (err: any) {
      setFormError(err?.message || 'Failed to save contact memory');
    }
  };

  return (
    <div className="min-h-screen bg-[#07070a] text-white p-6 md:p-10">
      <div className="mx-auto max-w-7xl space-y-8">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-white/10 pb-6">
          <div>
            <span className="text-xs uppercase tracking-widest text-emerald-400 font-semibold">
              Customer Graph & Memory
            </span>
            <h1 className="text-3xl font-bold tracking-tight mt-1">
              Durable Contacts & Contact Memory
            </h1>
            <p className="text-sm text-white/60 mt-1">
              E.164 normalized tenant contacts with credential-safe key/value
              memory entries shared across Voice and Chat agents.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="/dashboard/chat-agents"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-4 py-2 text-xs font-medium text-white hover:bg-white/10"
            >
              Chat Agents Studio
            </a>
            <button
              type="button"
              onClick={reload}
              className="rounded-xl border border-white/15 bg-white/[0.04] px-4 py-2 text-xs font-medium text-white hover:bg-white/10"
            >
              Refresh
            </button>
          </div>
        </div>

        {notice && (
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-200">
            {notice}
          </div>
        )}

        {(error || formError) && (
          <div
            role="alert"
            className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-xs text-red-200"
          >
            {formError || error}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left Column: Upsert Contact + Directory */}
          <div className="lg:col-span-5 space-y-6">
            <form
              onSubmit={handleCreateContact}
              className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 space-y-3"
            >
              <h2 className="text-sm font-semibold text-white">
                Add / Upsert Contact (E.164)
              </h2>
              <input
                type="text"
                placeholder="Phone (e.g. +14155550199)"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
              />
              <input
                type="text"
                placeholder="Full Name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
              />
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="email"
                  placeholder="Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                />
                <input
                  type="text"
                  placeholder="Company"
                  value={company}
                  onChange={(e) => setCompany(e.target.value)}
                  className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                />
              </div>
              <button
                type="submit"
                className="w-full rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500"
              >
                Save Durable Contact
              </button>
            </form>

            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <input
                  type="text"
                  placeholder="Search name, phone, company..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="flex-1 rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 text-xs text-white"
                />
                <select
                  value={lifecycleFilter}
                  onChange={(e) => setLifecycleFilter(e.target.value)}
                  className="rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 text-xs text-white"
                >
                  <option value="all">All</option>
                  <option value="active">Active</option>
                  <option value="archived">Archived</option>
                </select>
              </div>

              {loading ? (
                <div className="h-24 animate-pulse rounded-xl bg-white/5" />
              ) : contacts.length === 0 ? (
                <p className="text-xs text-white/40">No contacts found.</p>
              ) : (
                <div className="space-y-2">
                  {contacts.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setSelectedContact(c)}
                      className={`w-full text-left rounded-xl border p-3 transition ${
                        selectedContact?.id === c.id
                          ? 'border-emerald-500/50 bg-emerald-500/10'
                          : 'border-white/10 bg-black/40 hover:bg-white/[0.04]'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-white">
                          {c.name || c.phone}
                        </span>
                        <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] uppercase text-white/80">
                          {c.lifecycle}
                        </span>
                      </div>
                      <div className="text-xs text-white/50 mt-0.5 font-mono">
                        {c.phone} {c.company ? `• ${c.company}` : ''}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right Column: Contact Details & Durable Memory Entries */}
          <div className="lg:col-span-7 space-y-6">
            {selectedContact ? (
              <>
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h2 className="text-lg font-semibold text-white">
                        {selectedContact.name || 'Unnamed Contact'}
                      </h2>
                      <p className="text-xs text-white/50 font-mono">
                        {selectedContact.phone} • Source:{' '}
                        {selectedContact.source} • Lifecycle:{' '}
                        {selectedContact.lifecycle}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {selectedContact.lifecycle !== 'archived' ? (
                        <button
                          type="button"
                          onClick={() => archive(selectedContact.id)}
                          className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-200"
                        >
                          Archive
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => restore(selectedContact.id)}
                          className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-200"
                        >
                          Restore
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => remove(selectedContact.id)}
                        className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-200"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
                  <div>
                    <h3 className="text-sm font-semibold text-white">
                      Contact Memory Entries ({memoryEntries.length})
                    </h3>
                    <p className="text-xs text-white/50">
                      Durable key/value facts. Credential-like keys (api_key,
                      password, secret, token, ssn, cvv) are strictly refused.
                    </p>
                  </div>

                  <form
                    onSubmit={handleSaveMemory}
                    className="grid grid-cols-1 sm:grid-cols-12 gap-2"
                  >
                    <input
                      type="text"
                      placeholder="Key (e.g. preferred_language)"
                      value={memKey}
                      onChange={(e) => setMemKey(e.target.value)}
                      className="sm:col-span-4 rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white font-mono"
                    />
                    <input
                      type="text"
                      placeholder="Fact value (e.g. Prefers Bengali support)"
                      value={memValue}
                      onChange={(e) => setMemValue(e.target.value)}
                      className="sm:col-span-5 rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-xs text-white"
                    />
                    <input
                      type="number"
                      min="0"
                      max="100"
                      value={memImportance}
                      onChange={(e) => setMemImportance(e.target.value)}
                      className="sm:col-span-1 rounded-xl border border-white/10 bg-black/50 px-2 py-2 text-xs text-white"
                    />
                    <button
                      type="submit"
                      className="sm:col-span-2 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-500"
                    >
                      Save Fact
                    </button>
                  </form>

                  {memoryEntries.length === 0 ? (
                    <p className="text-xs text-white/40">
                      No memory facts stored for this contact yet.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {memoryEntries.map((m) => (
                        <div
                          key={m.id}
                          className="flex items-center justify-between rounded-xl border border-white/10 bg-black/40 p-3"
                        >
                          <div>
                            <div className="flex items-center gap-2">
                              <code className="text-xs font-semibold text-emerald-300">
                                {m.key}
                              </code>
                              <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/70">
                                {m.source} • imp {m.importance}
                              </span>
                            </div>
                            <p className="text-xs text-white/80 mt-1">
                              {m.value}
                            </p>
                          </div>
                          <button
                            type="button"
                            onClick={() =>
                              deleteMemory(selectedContact.id, m.key)
                            }
                            className="rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-1 text-xs text-red-200 hover:bg-red-500/20"
                          >
                            Delete
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-12 text-center text-sm text-white/40">
                Select or create a Contact to inspect and manage durable
                Contact Memory facts.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default ContactsPage;
