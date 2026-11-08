import React from 'react';
import {fireEvent, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi} from 'vitest';
import {RecoverContext} from './RecoverContext';
import type {Message} from '../../shared/contracts';

const source: Message = {id: 1815, account_id: 2, group_id: 21, account_name: '模拟账号', group_name: '模拟群',
  user_id: 1, sender_name: '模拟用户', source_user_id: null, source_message_id: null,
  sent_at: '2026-09-08 18:33:40', message_type: 'text', content_text: '「缺失的原文」 - - - 回复',
  is_deleted: 0, deleted_at: null, attachment_count: 0};
const submitName = '创建补采集任务（等待启动确认）';
async function setup() {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({id: 99, message: '已排队'}), {status: 202}));
  vi.stubGlobal('fetch', fetcher);
  render(<RecoverContext source={source}/>);
  const user = userEvent.setup();
  await user.click(screen.getByText('原文未在本地找到：创建有界补采集任务'));
  return {user, fetcher};
}
async function selectTime(user: ReturnType<typeof userEvent.setup>, label: string, value: string) {
  await user.click(screen.getByRole('button', {name: `${label}选择`}));
  const list = screen.getByRole('listbox', {name: label});
  expect(list).toHaveAttribute('size', '12');
  expect(within(list).getAllByRole('option')).toHaveLength(label.endsWith('小时') ? 24 : 60);
  await user.selectOptions(list, value);
}

describe('补采集独立日期与时间选择', () => {
  it('保留默认范围，修改日期/小时/分钟后提交同账号同群的精确本地范围', async () => {
    const {user, fetcher} = await setup();
    expect(screen.getByLabelText('补采集开始日期')).toHaveValue('2026-09-08');
    expect(screen.getByRole('button', {name: '补采集开始小时选择'})).toHaveTextContent('00 时');
    expect(screen.getByRole('button', {name: '补采集结束分钟选择'})).toHaveTextContent('33 分');
    expect(screen.getByLabelText('补采集结束日期')).toHaveAttribute('max', '2026-09-08');
    fireEvent.change(screen.getByLabelText('补采集开始日期'), {target: {value: '2026-09-07'}});
    await selectTime(user, '补采集开始小时', '09');
    await selectTime(user, '补采集开始分钟', '35');
    await selectTime(user, '补采集结束小时', '17');
    await selectTime(user, '补采集结束分钟', '59');
    expect(screen.getByRole('button', {name: submitName})).toBeDisabled();
    expect(fetcher).not.toHaveBeenCalled();
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', {name: submitName}));
    await screen.findByText('任务 #99：已排队');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/messages/1815/recover-context');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({account_id: 2, group_id: 21,
      range_start: '2026-09-07T09:35', range_end: '2026-09-08T17:59', confirmed: true});
    expect(screen.getByRole('checkbox')).not.toBeChecked();
  });
  it('每次修改日期、小时或分钟都要重新核对范围', async () => {
    const {user, fetcher} = await setup();
    for (const label of ['补采集开始', '补采集结束']) {
      for (const part of ['日期', '小时', '分钟']) {
        await user.click(screen.getByRole('checkbox'));
        if (part === '日期') fireEvent.change(screen.getByLabelText(`${label}日期`), {target: {value: '2026-09-07'}});
        else await selectTime(user, `${label}${part}`, '01');
        expect(screen.getByRole('checkbox')).not.toBeChecked();
        expect(screen.getByRole('button', {name: submitName})).toBeDisabled();
      }
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('空日期、倒置、超过七天或结束晚于引用消息时不创建任务', async () => {
    const {user, fetcher} = await setup();
    for (const date of ['', '2026-09-09', '2026-09-01']) {
      fireEvent.change(screen.getByLabelText('补采集开始日期'), {target: {value: date}});
      await user.click(screen.getByRole('checkbox'));
      expect(screen.getByRole('button', {name: submitName})).toBeDisabled();
    }
    fireEvent.change(screen.getByLabelText('补采集开始日期'), {target: {value: '2026-09-08'}});
    await selectTime(user, '补采集结束分钟', '34');
    await user.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('button', {name: submitName})).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('不能晚于引用消息');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('回收站消息不显示补采集入口', () => {
    const {container} = render(<RecoverContext source={{...source, is_deleted: 1}}/>);
    expect(container).toBeEmptyDOMElement();
  });
});
