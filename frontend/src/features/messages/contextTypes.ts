import type {Message} from '../../shared/contracts';
export type MessageRef = Pick<Message, 'id' | 'account_id' | 'group_id' | 'is_deleted'>;
export type ContextData = {target_id: number; account_id: number; group_id: number; deleted_only: boolean;
  items: Message[]; has_older: boolean; has_newer: boolean};

