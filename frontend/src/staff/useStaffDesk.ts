/**
 * Everything the staff screen needs, behind one hook: the queue, the package
 * being looked at, and the actions a person can take on it. Components
 * receive the result and only draw it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { api, type HandoffOutcome, type HandoffPackage, type HandoffSummary } from '../api/client'
import { useNow } from '../session/useNow'
import { describeProblem } from './words'

/** How often to look for new handoffs and changes to the one on screen. */
const POLL_INTERVAL_MS = 2000
const NAME_KEY = 'drivetruagent.staffName'

export type QueueTab = 'waiting' | 'closed'

export interface StaffDesk {
  status: 'loading' | 'ready' | 'failed'
  tab: QueueTab
  setTab: (tab: QueueTab) => void
  /** The handoffs in the chosen tab, longest waiting first. */
  queue: HandoffSummary[]
  waitingCount: number
  selectedId: string | null
  select: (handoffId: string) => void
  /** The package for the selected handoff. Null while loading or if none. */
  selected: HandoffPackage | null
  staffName: string
  setStaffName: (name: string) => void
  acknowledge: () => void
  resolve: (outcome: HandoffOutcome, note: string) => void
  /** An action is on its way to the backend. */
  busy: boolean
  /** Why the last action failed, in words. Null if it did not. */
  problem: string | null
  now: number
}

function handoffInAddress(): string | null {
  return new URLSearchParams(window.location.search).get('handoff')
}

export function useStaffDesk(): StaffDesk {
  const queryClient = useQueryClient()
  const now = useNow()
  const [tab, setTab] = useState<QueueTab>('waiting')
  const [chosenId, setChosenId] = useState<string | null>(handoffInAddress)
  const [staffName, setName] = useState(() => window.localStorage.getItem(NAME_KEY) ?? '')

  // No retries: the poll is the retry, and a backend that is down should be
  // said to be down now, not after several seconds of backing off.
  const queueQuery = useQuery({ queryKey: ['handoffs'], queryFn: api.handoffs, refetchInterval: POLL_INTERVAL_MS, retry: false })
  const all = queueQuery.data ?? []
  const waiting = all.filter((handoff) => handoff.status !== 'resolved')
  // Closed handoffs read best newest first; waiting ones, longest wait first.
  const queue = tab === 'waiting' ? waiting : all.filter((handoff) => handoff.status === 'resolved').reverse()

  // What was clicked wins while it exists; otherwise the top of the list.
  const chosen = all.find((handoff) => handoff.handoff_id === chosenId)
  const selectedId = chosen?.handoff_id ?? queue[0]?.handoff_id ?? null

  const packageKey = ['handoff', selectedId]
  const packageQuery = useQuery({
    queryKey: packageKey,
    queryFn: () => api.handoff(selectedId!),
    enabled: selectedId !== null,
    // A closed handoff cannot change, so there is nothing to keep looking for.
    refetchInterval: (query) => (query.state.data?.status === 'resolved' ? false : POLL_INTERVAL_MS),
  })

  const afterAction = (updated: HandoffPackage) => {
    // Stay on the handoff just acted on. Once closed it leaves the waiting
    // list, and without this the screen would jump to the next one before
    // the person had seen what happened to theirs.
    setChosenId(updated.handoff_id)
    queryClient.setQueryData(['handoff', updated.handoff_id], updated)
    return queryClient.invalidateQueries({ queryKey: ['handoffs'] })
  }
  const acknowledging = useMutation({
    mutationFn: () => api.acknowledgeHandoff(selectedId!, staffName.trim() || 'staff'),
    onSuccess: afterAction,
  })
  const resolving = useMutation({
    mutationFn: ({ outcome, note }: { outcome: HandoffOutcome; note: string }) =>
      api.resolveHandoff(selectedId!, outcome, note.trim() || null),
    onSuccess: afterAction,
    // Someone else may have closed it first: show what is true now.
    onError: () => queryClient.invalidateQueries({ queryKey: packageKey }),
  })

  const select = useCallback(
    (handoffId: string) => {
      setChosenId(handoffId)
      acknowledging.reset()
      resolving.reset()
      const address = new URL(window.location.href)
      address.searchParams.set('handoff', handoffId)
      window.history.replaceState(null, '', address)
    },
    [acknowledging, resolving],
  )

  return {
    status: queueQuery.isError ? 'failed' : queueQuery.isSuccess ? 'ready' : 'loading',
    tab,
    setTab,
    queue,
    waitingCount: waiting.length,
    selectedId,
    select,
    selected: packageQuery.data ?? null,
    staffName,
    setStaffName: useCallback((name: string) => {
      setName(name)
      window.localStorage.setItem(NAME_KEY, name)
    }, []),
    acknowledge: useCallback(() => {
      if (selectedId) acknowledging.mutate()
    }, [selectedId, acknowledging]),
    resolve: useCallback(
      (outcome: HandoffOutcome, note: string) => {
        if (selectedId) resolving.mutate({ outcome, note })
      },
      [selectedId, resolving],
    ),
    busy: acknowledging.isPending || resolving.isPending,
    problem: describeProblem(resolving.error ?? acknowledging.error),
    now,
  }
}
