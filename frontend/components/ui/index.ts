/**
 * Design system barrel.
 *
 * Pages import from `@/components/ui` only. Nothing outside this folder should
 * define a button, badge, table, or dialog: if a screen needs something new, it
 * gets added here first so every other screen inherits it.
 */

export { Button, Spinner } from "./Button";
export type { ButtonProps, ButtonSize, ButtonVariant } from "./Button";

export { Field, Input, Select, Switch, Textarea } from "./Form";
export type { FieldProps, InputProps, SelectProps, SwitchProps } from "./Form";

export {
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  PageHeader,
  StatCard,
} from "./Card";

export {
  AppointmentStatusBadge,
  Badge,
  CallOutcomeBadge,
  LanguageBadge,
  MessageStatusBadge,
} from "./Badge";
export type { BadgeTone } from "./Badge";

export { CellStack, Table } from "./Table";
export type { Column, TableProps } from "./Table";

export { Alert, EmptyState, PageLoading, Skeleton } from "./Feedback";
export type { AlertTone } from "./Feedback";

export { ConfirmDialog, Modal } from "./Modal";
export type { ModalProps, ModalSize } from "./Modal";

export { ToastProvider, useToast } from "./Toast";
export type { ToastTone } from "./Toast";

export { Pagination } from "./Pagination";

export { cn } from "./utils";
