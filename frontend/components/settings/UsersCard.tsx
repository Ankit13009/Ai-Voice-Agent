"use client";

import { useState } from "react";

import { businessApi } from "@/lib/api/endpoints";
import { useApiQuery, useMutation } from "@/lib/useApi";
import { formatRelative } from "@/lib/format";
import type { BusinessUser } from "@/types/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Field,
  Input,
  Table,
  useToast,
  type Column,
} from "@/components/ui";

/**
 * Dashboard users, and password recovery.
 *
 * There is no email service, so a self-serve "forgot password" link would have
 * nowhere to go. This matches how the product is actually run: the owner is on
 * the phone with their staff member, or with you, and reads out a new password.
 *
 * The password is generated rather than chosen, shown exactly once, forces a
 * change at first sign-in, and revokes every existing session for that user.
 */
export function UsersCard() {
  const toast = useToast();
  const users = useApiQuery((signal) => businessApi.listUsers(signal), []);

  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  // Held in state, not fetched: the server will never return it again.
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null);

  const addUser = useMutation(async () => {
    try {
      const { data } = await businessApi.createUser({
        email: email.trim(),
        full_name: fullName.trim(),
        role: "staff",
      });
      setIssued({ email: data.email, password: data.temporary_password });
      setEmail("");
      setFullName("");
      users.refetch();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  const resetPassword = useMutation(async (user: BusinessUser) => {
    try {
      const { data } = await businessApi.resetUserPassword(user.id);
      setIssued({ email: data.email, password: data.temporary_password });
      users.refetch();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  const columns: Array<Column<BusinessUser>> = [
    {
      key: "user",
      header: "User",
      render: (user) => (
        <div className="min-w-0">
          <p className="text-sm text-ink truncate">{user.email}</p>
          <p className="text-xs text-ink-subtle mt-0.5 capitalize">
            {user.role}
            {user.full_name ? ` · ${user.full_name}` : ""}
          </p>
        </div>
      ),
    },
    {
      key: "last_seen",
      header: "Last sign-in",
      hideOnMobile: true,
      render: (user) => (
        <span className="text-sm text-ink-muted">
          {user.last_login_at ? formatRelative(user.last_login_at) : "never"}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (user) => (
        <div className="flex flex-wrap gap-1.5">
          {user.is_locked && <Badge tone="danger">Locked</Badge>}
          {user.must_change_password && <Badge tone="warning">Must change password</Badge>}
          {!user.is_locked && !user.must_change_password && (
            <Badge tone="success" dot>
              Active
            </Badge>
          )}
        </div>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (user) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => resetPassword.run(user)}
          disabled={resetPassword.pending}
        >
          Reset password
        </Button>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Users"
        description="Who can sign in here. Resetting issues a one-time password, clears any lockout, and signs that user out everywhere."
      />

      {issued && (
        <CardBody className="border-b border-line">
          <Alert tone="success" title="One-time password">
            <p className="font-mono text-sm mt-1">
              {issued.email} &nbsp;&nbsp; {issued.password}
            </p>
            <p className="text-xs mt-1.5 opacity-80">
              Give this to them now. It cannot be shown again, and they must set their own
              password before they can use the dashboard.
            </p>
            <Button
              size="sm"
              variant="secondary"
              className="mt-2"
              onClick={() => {
                navigator.clipboard?.writeText(`${issued.email} / ${issued.password}`);
                toast.showSuccess(null, "Copied.");
              }}
            >
              Copy
            </Button>
          </Alert>
        </CardBody>
      )}

      <Table
        columns={columns}
        rows={(users.data ?? []) as BusinessUser[]}
        rowKey={(user) => user.id}
        loading={users.loading}
        empty={<p className="text-center text-sm text-ink-subtle">No users yet.</p>}
      />

      <CardBody className="border-t border-line grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
        <Field label="Email">
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="frontdesk@business.in"
          />
        </Field>
        <Field label="Name">
          <Input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Front Desk"
          />
        </Field>
        <Button
          variant="secondary"
          onClick={() => addUser.run()}
          loading={addUser.pending}
          disabled={!email.trim()}
        >
          Add user
        </Button>
      </CardBody>
    </Card>
  );
}
