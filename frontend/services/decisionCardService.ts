// KnowEvo decision-card API client (T-19). Thin wrapper over
// fetchWithAuth; every error surfaces as ApiError so pages can message.
// Same pipeline as the decision_card_render MCP tool - the panel, the
// Agent and the evaluation harness see the same card.
import { ApiError } from "./api";
import { fetchWithAuth } from "@/lib/auth";
import {
  buildDecisionCardRequest,
  type DecisionCardMode,
  type DecisionCardPayload,
} from "@/types/decisionCard";

const fetch = fetchWithAuth;

const BASE = "/api/knowevo/decision";

async function readResponse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null);
  if (response.ok) {
    return data as T;
  }
  const detail = data?.detail;
  throw new ApiError(
    data?.code ?? response.status,
    typeof detail === "string" ? detail : response.statusText
  );
}

export const decisionCardService = {
  async renderCard(payload: {
    question: string;
    ontologyVersion?: string;
    asOf?: string;
    mode?: DecisionCardMode;
  }): Promise<DecisionCardPayload> {
    const body = buildDecisionCardRequest(payload);
    const res = await fetch(`${BASE}/card`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return readResponse<DecisionCardPayload>(res);
  },
};
