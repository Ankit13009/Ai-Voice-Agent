/**
 * Every API endpoint, typed once.
 *
 * Components import from here rather than passing URL strings around, so a
 * renamed route or changed parameter is one edit and a compile error at each
 * call site, not a runtime 404 discovered in production.
 */

import { api, requestWithMessage, tokenStore } from "./client";
import type {
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
  Clinic,
  ClinicUpdateRequest,
  DashboardStats,
  Doctor,
  DoctorCreateRequest,
  DoctorUpdateRequest,
  GoogleAuthorizeResponse,
  GoogleCalendarStatus,
  LoginPayload,
  LoginRequest,
  MessageListQuery,
  OnboardClinicRequest,
  OnboardClinicResponse,
  Patient,
  PatientDetail,
  PatientListQuery,
  PatientUpdateRequest,
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

export const clinicApi = {
  me: (signal?: AbortSignal) => api.get<Clinic>(`${V1}/clinics/me`, undefined, signal),
  update: (payload: ClinicUpdateRequest) => api.patch<Clinic>(`${V1}/clinics/me`, payload),

  listDoctors: (signal?: AbortSignal) =>
    api.get<Doctor[]>(`${V1}/clinics/me/doctors`, undefined, signal),
  createDoctor: (payload: DoctorCreateRequest) =>
    api.post<Doctor>(`${V1}/clinics/me/doctors`, payload),
  updateDoctor: (id: string, payload: DoctorUpdateRequest) =>
    api.patch<Doctor>(`${V1}/clinics/me/doctors/${id}`, payload),
  deactivateDoctor: (id: string) => api.delete<null>(`${V1}/clinics/me/doctors/${id}`),
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

export const patientApi = {
  list: (query: PatientListQuery = {}, signal?: AbortSignal) =>
    api.list<Patient>(`${V1}/patients`, { ...query }, signal),
  get: (id: string, signal?: AbortSignal) =>
    api.get<PatientDetail>(`${V1}/patients/${id}`, undefined, signal),
  update: (id: string, payload: PatientUpdateRequest) =>
    api.patch<Patient>(`${V1}/patients/${id}`, payload),
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

export const onboardingApi = {
  createClinic: (payload: OnboardClinicRequest) =>
    api.post<OnboardClinicResponse>(`${V1}/onboarding/clinics`, payload),
};
