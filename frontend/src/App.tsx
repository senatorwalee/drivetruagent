import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'

import { CALL_PATH, CALLERS_PATH, CALLS_PATH, CONVERSATIONS_PATH, INSIGHTS_PATH, LIVE_PATH, MENU_PATH, PICKUPS_PATH, STAFF_PATH, usePath } from './app/navigation'
import { CallersPage } from './callers/CallersPage'
import { PickupsPage } from './callers/PickupsPage'
import { ConversationsPage } from './conversations/ConversationsPage'
import { HomePage } from './home/HomePage'
import { InsightsPage } from './insights/InsightsPage'
import { MenuPage } from './menu-admin/MenuPage'
import { StaffPage } from './staff/StaffPage'
import { LiveSessionPage } from './views/LiveSessionPage'
import { WebPhonePage } from './web-phone/WebPhonePage'

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      // The page decides when data is refreshed; a window regaining focus is
      // not a reason to refetch a session.
      queries: { refetchOnWindowFocus: false },
    },
  })
}

export function App() {
  // Created once per mounted app, not once per module, so nothing fetched by
  // one instance (or one test) can leak into another.
  const [queryClient] = useState(createQueryClient)

  const path = usePath()

  return (
    <QueryClientProvider client={queryClient}>
      {path === STAFF_PATH ? (
        <StaffPage />
      ) : path === PICKUPS_PATH ? (
        <PickupsPage />
      ) : path === CALLERS_PATH ? (
        <CallersPage />
      ) : path === CONVERSATIONS_PATH ? (
        <ConversationsPage />
      ) : path === INSIGHTS_PATH ? (
        <InsightsPage />
      ) : path === MENU_PATH ? (
        <MenuPage />
      ) : path === CALLS_PATH ? (
        <LiveSessionPage follow="phone" />
      ) : path === CALL_PATH ? (
        <WebPhonePage />
      ) : path === LIVE_PATH ? (
        <LiveSessionPage />
      ) : (
        // The home page, and where an unknown address lands.
        <HomePage />
      )}
    </QueryClientProvider>
  )
}
