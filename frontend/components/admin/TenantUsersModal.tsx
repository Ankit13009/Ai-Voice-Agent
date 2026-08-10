"use client";

import { useEffect, useState } from "react";

import { adminApi } from "@/lib/api/endpoints";
import { formatRelative } from "@/lib/format";
import type { AdminTenantUser, AdminTenantUsers, IssuedPassword } from "@/types/api";
import {
  Alert,
  Badge,
  Button,
  Modal,
  Table,
  useToast,
  type Column,
} from "@/components/ui";

/**
 * The operator's rescue path for a client who cannot sign in.
 *
 * There is no email service, so no self-serve password reset exists, and an
 * owner who forgets their password is locked out of their own business
 * entirely. This is the only route back in short of editing the database, and
 * it is how the product is actually run: the clinic phones you, and you read
 * out a new password.
 *
 * Fetched on open rather than with the clients list, because it is needed on
 * the rare day something is wrong and would otherwise be one request per
 * tenant on every page load.
 */
export function TenantUsersModal({
  businessId,
  businessName,
  onClose,
}: {
  businessId: string | null;
  businessName: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [data, setData] = useState<AdminTenantUsers | null>(null);
  const [loading, setLoading] = useState(false);
  const [resettingId, setResettingId] = useState("");
  // Held in state because the server will never return this password again.
  const [issued, setIssued] = useState<IssuedPassword | null>(null);

  useEffect(() => {
    if (!businessId) return;
    let cancelled = false;

    setLoading(true);
    setIssued(null);
    adminApi
      .listTenantUsers(businessId)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((error) => {
        if (!cancelled) toast.showApiError(error);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [businessId, toast]);

  const reset = async (user: AdminTenantUser) => {
    setResettingId(user.id);
    try {
      const { data: password } = await adminApi.resetUserPassword(user.id);
      setIssued(password);
      if (businessId) setData(await adminApi.listTenantUsers(businessId));
    } catch (error) {
      toast.showApiError(error);
    } finally {
      setResettingId("");
    }
  };

  const columns: Array<Column<AdminTenantUser>> = [
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
          {user.must_change_password && <Badge tone="warning">Must change</Badge>}
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
          onClick={() => reset(user)}
          loading={resettingId === user.id}
          disabled={Boolean(resettingId)}
        >
          Reset password
        </Button>
      ),
    },
  ];

  return (
    <Modal
      open={Boolean(businessId)}
      onClose={onClose}
      title={`Users at ${businessName}`}
      description="Reset a password when a client cannot sign in. This clears any lockout and signs that user out everywhere."
      size="lg"
      busy={Boolean(resettingId)}
    >
      {issued && (
        <Alert tone="success" title="One-time password">
          <p className="font-mono text-sm mt-1 break-all">
            {issued.email} &nbsp;&nbsp; {issued.temporary_password}
          </p>
          <p className="text-xs mt-1.5 opacity-80">
            Read this out now. It cannot be shown again, and they will be asked to
            change it when they sign in.
          </p>
          <Button
            size="sm"
            variant="secondary"
            className="mt-2"
            onClick={() => {
              navigator.clipboard?.writeText(
                `${issued.email} / ${issued.temporary_password}`,
              );
              toast.showSuccess(null, "Copied.");
            }}
          >
            Copy
          </Button>
        </Alert>
      )}

      <div className="mt-4">
        <Table
          columns={columns}
          rows={data?.users ?? []}
          rowKey={(user) => user.id}
          loading={loading}
          empty={<p className="text-center text-sm text-ink-subtle">No users.</p>}
        />
      </div>
    </Modal>
  );
}
