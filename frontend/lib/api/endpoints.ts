/**
 * Every API endpoint, typed once.
 *
 * Components import from here rather than passing URL strings around, so a
 * renamed route or changed parameter is one edit and a compile error at each
 * call site, not a runtime 404 discovered in production.
 */

import { api, requestWithMessage, tokenStore } from "./client";
import type {
  AdminBusinessRow,
  AdminTenantUsers,
  IssuedPassword,
  Appointment,
  AppointmentCancelRequest,
  AppointmentCreateRequest,
  AppointmentListQuery,
  AppointmentRescheduleRequest,
  AvailabilityQuery,
  Call,
  CallDetail,
  CallListQuery,
  ChangePasswordRequest,
  Business,
  BusinessTypePreset,
  BusinessUser,
  BusinessUpdateRequest,
  DashboardStats,
  StaffMember,
  StaffMemberCreateRequest,
  StaffMemberUpdateRequest,
  TemporaryPassword,
  TestCallConfig,
  GoogleAuthorizeResponse,
  GoogleCalendarStatus,
  LoginPayload,
  LoginRequest,
  MessageListQuery,
  OnboardBusinessRequest,
  OnboardBusinessResponse,
  OutboundTestCallResult,
  Customer,
  CustomerDetail,
  CustomerListQuery,
  CustomerUpdateRequest,
  PlatformStats,
  Slot,
  User,
  WhatsAppMessage,
} from "@/types/api";

const V1 = "/api/v1";

export const authApi = {
  async login(payload: LoginRequest): Promise<LoginPayload> {
    // skipAuth: a stale token must not be sent, and a 401 here means bad
    // credentials, not an expired session to refresh.
    const { data } = await requestWithMessage<LoginPayload>(`${V1}/auth/login`, {
      method: "POST",
      body: payload,
      skipAuth: true,
    });
    tokenStore.set(data.tokens);
    return data;
  },

  async logout(): Promise<void> {
    const refresh = tokenStore.refresh;
    try {
      if (refresh) {
        await api.post(`${V1}/auth/logout`, { refresh_token: refresh });
      }
    } finally {
      // Clear locally even if the server call fails: the user asked to sign out.
      tokenStore.clear();
    }
  },

  me: () => api.get<User>(`${V1}/auth/me`),

  changePassword: (payload: ChangePasswordRequest) =>
    api.post<null>(`${V1}/auth/change-password`, payload),
};

export const dashboardApi = {
  stats: (signal?: AbortSignal) =>
    api.get<DashboardStats>(`${V1}/dashboard/stats`, undefined, signal),
};

export const businessApi = {
  me: (signal?: AbortSignal) => api.get<Business>(`${V1}/businesses/me`, undefined, signal),
  update: (payload: BusinessUpdateRequest) => api.patch<Business>(`${V1}/businesses/me`, payload),

  /** Public key + assistant id so the browser can talk to this tenant's agent. */
  /** Rings a real phone with this business's agent. Costs money per press. */
  outboundTestCall: (phone_number: string) =>
    api.post<OutboundTestCallResult>(`${V1}/businesses/me/test-call/outbound`, {
      phone_number,
    }),

  testCallConfig: (signal?: AbortSignal) =>
    api.get<TestCallConfig>(`${V1}/businesses/me/test-call`, undefined, signal),

  listUsers: (signal?: AbortSignal) =>
    api.get<BusinessUser[]>(`${V1}/businesses/me/users`, undefined, signal),
  createUser: (payload: { email: string; full_name?: string; role?: "owner" | "staff" }) =>
    api.post<TemporaryPassword>(`${V1}/businesses/me/users`, payload),
  /** Issues a one-time password and revokes the user's existing sessions. */
  resetUserPassword: (userId: string) =>
    api.post<TemporaryPassword>(`${V1}/businesses/me/users/${userId}/reset-password`),

  listStaffMembers: (signal?: AbortSignal) =>
    api.get<StaffMember[]>(`${V1}/businesses/me/staff`, undefined, signal),
  createStaffMember: (payload: StaffMemberCreateRequest) =>
    api.post<StaffMember>(`${V1}/businesses/me/staff`, payload),
  updateStaffMember: (id: string, payload: StaffMemberUpdateRequest) =>
    api.patch<StaffMember>(`${V1}/businesses/me/staff/${id}`, payload),
  deactivateStaffMember: (id: string) => api.delete<null>(`${V1}/businesses/me/staff/${id}`),
};

export const appointmentApi = {
  list: (query: AppointmentListQuery = {}, signal?: AbortSignal) =>
    api.list<Appointment>(`${V1}/appointments`, { ...query }, signal),

  get: (id: string, signal?: AbortSignal) =>
    api.get<Appointment>(`${V1}/appointments/${id}`, undefined, signal),

  availability: (query: AvailabilityQuery = {}, signal?: AbortSignal) =>
    api.get<Slot[]>(`${V1}/appointments/availability`, { ...query }, signal),

  create: (payload: AppointmentCreateRequest) =>
    api.post<Appointment>(`${V1}/appointments`, payload),

  reschedule: (id: string, payload: AppointmentRescheduleRequest) =>
    api.patch<Appointment>(`${V1}/appointments/${id}/reschedule`, payload),

  cancel: (id: string, payload: AppointmentCancelRequest = {}) =>
    api.patch<Appointment>(`${V1}/appointments/${id}/cancel`, payload),
};

export const callApi = {
  list: (query: CallListQuery = {}, signal?: AbortSignal) =>
    api.list<Call>(`${V1}/calls`, { ...query }, signal),
  get: (id: string, signal?: AbortSignal) =>
    api.get<CallDetail>(`${V1}/calls/${id}`, undefined, signal),
};

export const customerApi = {
  list: (query: CustomerListQuery = {}, signal?: AbortSignal) =>
    api.list<Customer>(`${V1}/customers`, { ...query }, signal),
  get: (id: string, signal?: AbortSignal) =>
    api.get<CustomerDetail>(`${V1}/customers/${id}`, undefined, signal),
  update: (id: string, payload: CustomerUpdateRequest) =>
    api.patch<Customer>(`${V1}/customers/${id}`, payload),
};

export const messageApi = {
  list: (query: MessageListQuery = {}, signal?: AbortSignal) =>
    api.list<WhatsAppMessage>(`${V1}/messages`, { ...query }, signal),
  retry: (id: string) => api.post<WhatsAppMessage>(`${V1}/messages/${id}/retry`),
};

export const integrationApi = {
  googleStatus: (signal?: AbortSignal) =>
    api.get<GoogleCalendarStatus>(`${V1}/integrations/google/status`, undefined, signal),
  googleAuthorize: () =>
    api.get<GoogleAuthorizeResponse>(`${V1}/integrations/google/authorize`),
  googleDisconnect: () => api.delete<null>(`${V1}/integrations/google`),
};

export const adminApi = {
  listBusinesses: (signal?: AbortSignal) =>
    api.get<AdminBusinessRow[]>(`${V1}/admin/businesses`, undefined, signal),
  stats: (signal?: AbortSignal) =>
    api.get<PlatformStats>(`${V1}/admin/stats`, undefined, signal),

  // Operator escalation: an owner locked out of their own tenant has no
  // self-serve route back in, because there is no email service to send one.
  listTenantUsers: (businessId: string, signal?: AbortSignal) =>
    api.get<AdminTenantUsers>(
      `${V1}/admin/businesses/${businessId}/users`,
      undefined,
      signal,
    ),
  resetUserPassword: (userId: string) =>
    api.post<IssuedPassword>(`${V1}/admin/users/${userId}/reset-password`),
};

export const onboardingApi = {
  businessTypes: (signal?: AbortSignal) =>
    api.get<BusinessTypePreset[]>(`${V1}/onboarding/business-types`, undefined, signal),

  createBusiness: (payload: OnboardBusinessRequest) =>
    api.post<OnboardBusinessResponse>(`${V1}/onboarding/businesses`, payload),
};
