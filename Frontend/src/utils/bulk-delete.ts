import type { BulkDeleteResource, BulkDeleteResult } from '../../typefiles';

/**
 * Delete as many resources as possible, retrying constraint failures through
 * the endpoint's explicit force-delete operation.
 */
export async function bulkDeleteWithFallback(
  ids: number[],
  resource: BulkDeleteResource,
): Promise<BulkDeleteResult> {
  const results = await Promise.allSettled(ids.map(async (id) => {
    try {
      await resource.delete(id);
    } catch {
      await resource.forceDelete(id);
    }
  }));

  const succeeded = results.filter((result) => result.status === 'fulfilled').length;
  return { succeeded, failed: results.length - succeeded };
}
