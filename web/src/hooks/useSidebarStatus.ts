import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { StatusResponse } from "@/lib/api";

const POLL_MS = 10_000;

/**
 * Light-weight status poll for the app shell (sidebar). The Status page uses
 * its own faster interval; we keep this slower to avoid duplicate load.
 *
 * Returns the latest status plus a `refresh` to force an immediate refetch —
 * used after lifecycle actions (e.g. turning the gateway on) so the sidebar
 * reflects the new state without waiting for the next poll tick.
 */
export function useSidebarStatus() {
  const [status, setStatus] = useState<StatusResponse | null>(null);

  const refresh = useCallback(() => {
    api
      .getStatus()
      .then(setStatus)
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return { status, refresh };
}
