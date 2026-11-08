import React from 'react';
import {render,screen} from '@testing-library/react';
import {it,expect,vi} from 'vitest';
import {ViewNavigation} from '../../shared/ViewNavigation';
it('提供独立的统计、日志与图片文字搜索入口',()=>{
 render(<ViewNavigation value="active" onChange={vi.fn()}/>);
 for(const name of ['统计仪表盘','操作日志','图片文字搜索'])expect(screen.getByRole('button',{name})).toBeInTheDocument();
});
