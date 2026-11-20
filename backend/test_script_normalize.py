import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import intake
from script_normalize import normalize_script, REVISION
from test_intake import docx


class ScriptNormalization(unittest.TestCase):
    def lines(self, text):
        return [target['text'] for target in normalize_script(text)['targets']]

    def test_known_speakers_colon_quotes_and_actions(self):
        text = '1. **达人（拿起虾饺）：**“这个虾饺真好吃”（花字：好吃）\n- 闺蜜：你不是说只吃一口吗？'
        result = normalize_script(text)
        self.assertEqual(result['sourceText'], text)
        self.assertEqual(result['revision'], REVISION)
        self.assertEqual(self.lines(text), ['这个虾饺真好吃', '你不是说只吃一口吗？'])
        self.assertTrue(result['strictReady'])
        self.assertTrue(result['targets'][1]['dialogue'])

    def test_multiple_speakers_in_same_paragraph(self):
        self.assertEqual(self.lines('达人：“好吃！” 闺蜜：“我也要！”'), ['好吃！', '我也要！'])
        self.assertEqual(self.lines('达人：这个好吃 闺蜜：真的好吃'), ['这个好吃', '真的好吃'])

    def test_speaker_on_previous_line_and_continuation(self):
        self.assertEqual(self.lines('【达人】\n“这个很香！”\n我还想再吃一口。'), ['这个很香！', '我还想再吃一口。'])

    def test_unknown_names_in_declared_roles_or_quotes(self):
        self.assertEqual(self.lines('出镜演员：小王、李明\n小王：这里真漂亮！\n李明：对呀！'), ['这里真漂亮！', '对呀！'])
        self.assertEqual(self.lines('小王：“我还想再吃一口。”'), ['我还想再吃一口。'])
        self.assertEqual(self.lines('小林：今天来爬山！\n小林：这里真漂亮。'), ['今天来爬山！', '这里真漂亮。'])
        self.assertEqual(self.lines('颜色：我喜欢红色\n颜色：我喜欢蓝色'), [])

    def test_metadata_not_inferred_as_speaker(self):
        result = normalize_script('品牌：真的好吃\n场景：我在餐厅\n编导：你说这个特别香\n标题：我来广州了！\nAI音：双重滑嫩~\n花字：我好喜欢')
        self.assertFalse(result['strictReady'])
        self.assertEqual(result['count'], 0)
        self.assertIn('无需手动改格式', result['warnings'][0])

    def test_repeated_unquoted_real_names_are_dialogue_without_manual_formatting(self):
        source = '小周：今天来到示例烤肉。\n小周：这个羊肉串真的好香！\n陈乐浠：我也想尝尝。\n陈乐浠：我还要一串！'
        result = normalize_script(source)
        self.assertEqual([t['speaker'] for t in result['targets']], ['小周','小周','陈乐浠','陈乐浠'])
        self.assertEqual(self.lines(source), ['今天来到示例烤肉。','这个羊肉串真的好香！','我也想尝尝。','我还要一串！'])
        self.assertTrue(result['strictReady'])
        self.assertEqual(result['sourceText'], source)

    def test_repeated_product_labels_do_not_become_people(self):
        source = '颜色：我喜欢红色\n颜色：我喜欢蓝色\n口味：这个很香\n口味：这个很甜\n环境：这里真好看\n环境：我觉得这里很舒服'
        self.assertEqual(self.lines(source), [])

    def test_stage_directions_removed_and_source_retained(self):
        text = '达人：（打开菠萝包）哇好好看！（闻）水牛奶的味道（吃）广东人说，吃着不甜！\n达人：（拿起商品）我想要！（又看到另一个）这个我也想要！'
        self.assertEqual(self.lines(text), ['哇好好看！水牛奶的味道广东人说，吃着不甜！', '我想要！这个我也想要！'])
        self.assertEqual(normalize_script(text)['sourceText'], text)

    def test_stage_only_or_abstract_brief_not_generated(self):
        text = '达人试吃，突出食物好吃，保留和闺蜜的互动。\n达人：\n（拿起虾饺）\n拍摄：拿起这个杯子\n请帮我确认价格'
        self.assertEqual(self.lines(text), [])

    def test_role_prefix_does_not_turn_edit_notes_into_dialogue(self):
        self.assertEqual(self.lines('达人的剪辑需求：保留好看的表情。\n达人觉得这个可以吗：拍一下吧'), [])

    def test_metadata_ends_unlabelled_continuation(self):
        self.assertEqual(self.lines('达人：这个很好吃。\n拍摄说明：转场到下一家店。\n这道菜需要多拍一些特写。'), ['这个很好吃。'])
        self.assertEqual(self.lines('达人：这个很好吃。\n场景：餐厅\n这个镜头用长焦拍。'), ['这个很好吃。'])

    def test_non_performance_labels_split_same_paragraph(self):
        self.assertEqual(self.lines('达人：这个真好吃！AI音：太香了！'), ['这个真好吃！'])
        self.assertEqual(self.lines('达人：好吃。导演：再来一遍。闺蜜：我也要。'), ['好吃。', '我也要。'])
        self.assertEqual(self.lines('达人：好吃！拍摄方式：你看这里要特写。闺蜜：我也觉得！'), ['好吃！', '我也觉得！'])

    def test_spoken_parenthetical_not_blindly_removed(self):
        self.assertEqual(self.lines('达人：这份套餐（两个人吃）刚刚好。'), ['这份套餐（两个人吃）刚刚好。'])

    def test_old_flattened_script_actions_and_tails(self):
        text = '脚本规划 Video script\n序号\n时间\n场景\n文案\n拍摄方式\n备注\n1\n0-30s\n餐厅\n达人点菜：我最爱大虾饺\n上菜画面\n吃肠粉：特别好吃！\n吃虾饺：好饱满的虾饺\n达人：广州真的很好。\n（插入广交会空镜）\n我现在懂了。\n所以我不想走了！\n门头→环境→重点菜→达人试吃'
        self.assertEqual(self.lines(text), ['我最爱大虾饺', '特别好吃！', '好饱满的虾饺', '广州真的很好。', '我现在懂了。', '所以我不想走了！'])

    def test_normalized_text_can_be_parsed_again(self):
        source = '| 角色 | 台词 |\n| --- | --- |\n| 小林 | 我来试试！ |\n| 闺蜜 | 你也尝尝！ |'
        first = normalize_script(source)
        second = normalize_script(first['normalizedText'])
        self.assertEqual([t['text'] for t in first['targets']], [t['text'] for t in second['targets']])

    def test_legacy_and_plain_mixed_order_source_link_and_interaction(self):
        text = 'C1｜角色意图：达人闺蜜互动｜台词意图：好多榴莲酱，拿包纸吧\n素材：a.mov｜源 900—999s\n达人：该省省，该花花~'
        result = normalize_script(text)
        self.assertEqual(result['count'], 2)
        self.assertTrue(result['targets'][0]['dialogue'])
        self.assertEqual(result['targets'][0]['sourceName'], 'a.mov')
        self.assertEqual(result['targets'][1]['text'], '该省省，该花花~')

    def test_reviewed_b_list_retains_content_sources_and_specific_dialogue(self):
        source = '\n'.join([
            'timebase: 30fps，原成片仅供参考',
            '【S01 早茶】成片 00:00:00—00:10:00',
            'C001｜角色意图：达人｜台词意图：虾饺真饱满！',
            '素材：吃虾饺.MOV｜源 9.000—12.000s｜成片 00:01:00',
            'C002｜角色意图：达人｜台词意图：这里也有榴莲酥。',
            '素材：吃榴莲酥.MOV｜源 3.000—5.000s｜成片 00:05:00',
            '【S02 正餐】成片 00:10:00—00:20:00',
            'C003｜角色意图：达人｜台词意图：还加了榴莲。',
            '素材：传统点心加榴莲.MOV｜源 2.000—5.000s｜成片 00:10:00',
            'C004｜角色意图：达人与闺蜜｜台词意图：达人：好多酱。闺蜜：拿包纸吧！',
            '素材：接话.MOV｜源 1.000—6.000s｜成片 00:14:00',
            '【第二轨画面 静音】',
            'B01｜虾饺｜IMG_1.MOV｜源 1.000—3.300s｜成片 00:02:06',
            'B02｜榴莲糖沙翁｜IMG_2.MOV｜源 4.200—6.000s｜成片 00:11:00'])
        result = normalize_script(source)
        self.assertEqual(result['sourceText'], source)
        self.assertEqual([t['text'] for t in result['targets']],
                         ['虾饺真饱满！', '这里也有榴莲酥。', '还加了榴莲。', '达人：好多酱。闺蜜：拿包纸吧！'])
        self.assertTrue(result['targets'][3]['dialogue'])
        self.assertEqual([t['scene'] for t in result['targets']], ['S01 早茶'] * 2 + ['S02 正餐'] * 2)
        shots = result['shotRequirements']
        self.assertEqual([s['text'] for s in shots], ['虾饺', '榴莲糖沙翁'])
        self.assertEqual([s['targetIds'] for s in shots], [[1], [3]])
        self.assertEqual(shots[0]['sourceName'], 'IMG_1.MOV')
        self.assertEqual(shots[0]['sourceRange'], {'in': 1.0, 'out': 3.3})
        self.assertEqual(shots[1]['originalTimelineReference'], '成片 00:11:00')
        for shot in shots:
            self.assertEqual(shot['requiredTrack'], 'V2')
            self.assertEqual(shot['anchorMode'], 'targets-only')
            self.assertEqual(shot['associationStatus'], 'matched')
            self.assertNotIn('at', shot)
            self.assertNotIn('start', shot)
            self.assertNotIn('end', shot)
            self.assertIn(shot['originalEditId'] + '｜', shot['sourceRefs'][0]['text'])

    def test_reviewed_operational_policies_are_neither_speech_nor_shots(self):
        policies = ['durationRangeSeconds: [120, 180]',
                    'directorInstructions: 排除倒数、看镜头、再来一遍',
                    'rolePolicy: 闺蜜接话必须保留',
                    'repetitionPolicy: 保护表情与动作',
                    'brollPolicy: 已观察到画面；V2静音；不得盖第一口反应',
                    'timebase: 30fps', 'audioPolicy: 不生成替代口播']
        source = '\n'.join(policies + ['【S01 正餐】成片 00:00:00—00:10:00',
            'C001｜角色意图：达人｜台词意图：烧鹅好香！',
            '【第二轨画面 静音】', 'B01｜烧鹅｜鹅.MOV｜源 1.000—3.000s｜成片 00:02:00',
            '【删除和压缩说明】', '保留纸巾梗整段动作；其余未入选保留原素材。',
            '保留示例餐厅及示例广场场景，取消过时段落。',
            '【审片重点】不得仅凭转写确认达人身份，检查切词。'])
        result = normalize_script(source)
        self.assertEqual([t['text'] for t in result['targets']], ['烧鹅好香！'])
        self.assertEqual([s['text'] for s in result['shotRequirements']], ['烧鹅'])
        excluded = [r['text'] for r in result['excludedContent'] if r['kind'] == 'production_policy']
        for policy in policies:
            self.assertIn(policy, excluded)
        self.assertIn('保留纸巾梗整段动作；其余未入选保留原素材。', excluded)

    def test_reviewed_shot_source_name_matches_dialogue_source_without_rewriting_words(self):
        source = ('timebase: 30fps\n【S01 早茶】成片 00:00:00—00:10:00\n'
                  'C001｜角色意图：达人｜台词意图：哇，好好看！\n素材：打开菠萝包.MOV\n'
                  'C002｜角色意图：达人｜台词意图：这个真好吃！\n素材：吃虾饺.MOV\n'
                  '【第二轨画面 静音】\nB01｜菠萝包｜空镜.MOV｜源 1.000—3.000s｜成片 00:02:00')
        result = normalize_script(source)
        self.assertEqual(result['shotRequirements'][0]['targetIds'], [1])
        self.assertEqual(result['targets'][0]['text'], '哇，好好看！')

    def test_reviewed_chapter_environment_never_associates_global_targets(self):
        source = ('timebase: 30fps\n【S01 开场】成片 00:00:00—00:10:00\n达人：今天来玩！\n'
                  '【S02 店内】成片 00:10:00—00:20:00\n达人：正餐真讲究。\n达人：我来安排。\n'
                  '【第二轨画面 静音】\nB01｜店内环境｜空镜.MOV｜源 1.000—3.000s｜成片 00:11:00')
        shot = normalize_script(source)['shotRequirements'][0]
        self.assertEqual(shot['targetIds'], [2, 3])
        self.assertEqual(shot['associationEvidence']['sharedSubjectLength'], 0)

    def test_reviewed_invalid_ambiguous_or_cross_chapter_times_never_fall_back(self):
        beginning = ('timebase: 30fps\n【S01 早茶】成片 00:00:00—00:10:00\n达人：虾饺真好吃！\n'
                     '【S02 正餐】成片 00:10:00—00:20:00\n达人：烧鹅真好吃！\n【第二轨画面 静音】\n')
        for reference in ('00:09:00—00:11:00', '00:20:00', '00:22:00', '00:01:30',
                          '00:01:00—错误', '未知', '00:01:000'):
            with self.subTest(reference=reference):
                result = normalize_script(beginning + 'B01｜虾饺｜空镜.MOV｜源 1.000—3.000s｜成片 ' + reference)
                shot = result['shotRequirements'][0]
                self.assertEqual(shot['targetIds'], [])
                self.assertEqual(shot['scene'], '')
                self.assertEqual(shot['associationStatus'], 'unanchored')
                self.assertTrue(any('待审' in message for message in result['warnings']))
        overlapping = beginning.replace('00:10:00—00:20:00', '00:05:00—00:20:00')
        self.assertEqual(normalize_script(overlapping + 'B01｜虾饺｜空镜.MOV｜成片 00:06:00')['shotRequirements'][0]['targetIds'], [])

    def test_reviewed_boundary_point_maps_following_chapter_not_previous(self):
        source = ('timebase: 30fps\n【S01 早茶】成片 00:00:00—00:10:00\n达人：真好吃！\n'
                  '【S02 正餐】成片 00:10:00—00:20:00\n达人：这里真漂亮！\n'
                  '【第二轨画面 静音】\nB01｜店内环境｜空镜.MOV｜成片 00:10:00')
        self.assertEqual(normalize_script(source)['shotRequirements'][0]['targetIds'], [2])

    def test_reviewed_timecodes_require_unambiguous_declared_fps(self):
        source = ('【S01 早茶】成片 00:00:00—00:10:00\n达人：虾饺真好吃！\n'
                  '【第二轨画面 静音】\nB01｜虾饺｜空镜.MOV｜成片 00:02:00')
        for prefix in ('', 'timebase: 30fps、25fps\n'):
            self.assertEqual(normalize_script(prefix + source)['shotRequirements'][0]['targetIds'], [])

    def test_reviewed_without_old_times_requires_unique_content_or_explicit_chapter(self):
        source = ('【S01 早茶】\n达人：这个虾饺真好吃！\n【S02 正餐】\n达人：这里有榴莲。\n'
                  '【第二轨画面 静音】\nB01｜虾饺｜空镜.MOV')
        self.assertEqual(normalize_script(source)['shotRequirements'][0]['targetIds'], [1])
        ambiguous = source.replace('这里有榴莲。', '这里也有虾饺。')
        self.assertEqual(normalize_script(ambiguous)['shotRequirements'][0]['targetIds'], [])
        explicit = source.split('【第二轨画面 静音】')[0] + 'B01｜店内环境｜空镜.MOV'
        self.assertEqual(normalize_script(explicit)['shotRequirements'][0]['targetIds'], [2])

    def test_reviewed_invalid_source_range_is_not_a_valid_cut(self):
        source = '【S01 早茶】\n达人：虾饺真好吃！\nB01｜虾饺｜空镜.MOV｜源 '
        for span in ('3.000—1.000s', '-1.000—3.000s', 'NaN—3.000s'):
            with self.subTest(span=span):
                shot = normalize_script(source + span)['shotRequirements'][0]
                self.assertEqual(shot['sourceName'], '空镜.MOV')
                self.assertNotIn('sourceRange', shot)

    def test_markdown_multicolumn_table_no_notes_as_speech(self):
        text = '| 镜头 | 角色 | 台词 | 备注 |\n| --- | --- | --- | --- |\n| 1 | 小林 | 我来尝尝。 | 这个镜头要近一点 |\n| 2 | 闺蜜 | 你觉得怎么样？ | 保留笑声 |\n| 3 | 编导 | 你说这个好吃 | 示范 |'
        result = normalize_script(text)
        self.assertEqual(self.lines(text), ['我来尝尝。', '你觉得怎么样？'])
        self.assertEqual(result['targets'][0]['speaker'], '小林')

    def test_six_column_script_table_action_labels_and_unlabelled_tail(self):
        text = '序号\t时间\t场景\t文案\t拍摄方式\t备注\n1\t0-10s\t餐厅\t达人点菜：我最爱大虾饺<br>吃肠粉：特别好吃！<br>店内人流画面<br>达人：广州真的很好。<br>我现在懂了。<br>所以我不想走了！\t你看这里机位要低\t我觉得改成近景'
        self.assertEqual(self.lines(text), ['我最爱大虾饺', '特别好吃！', '广州真的很好。', '我现在懂了。', '所以我不想走了！'])

    def test_narration_is_dialogue_but_synthetic_audio_is_not(self):
        self.assertEqual(self.lines('达人旁白：正餐吃得更讲究\nVO：今天来到广州。\nAI音：双重滑嫩'), ['正餐吃得更讲究', '今天来到广州。'])

    def test_plain_table_two_columns(self):
        self.assertEqual(self.lines('达人\t我还想再来一份！\n闺蜜\t你吃得完吗？'), ['我还想再来一份！', '你吃得完吗？'])

    def test_role_column_is_not_permission_to_match_action_notes(self):
        for value in ('店内人流画面', '达人拿起这个杯子', '拿起这个杯子', '达人试吃后点头'):
            with self.subTest(value=value):
                self.assertEqual(self.lines('| 人物 | 台词 |\n| 达人 | ' + value + ' |'), [])
        self.assertEqual(self.lines('| 人物 | 台词 |\n| 达人 | 真香 |\n| 闺蜜 | 嗯 |'), ['真香', '嗯'])

    def test_no_dialogue_or_empty_is_reported_without_fabrication(self):
        for text in ('', '内容创意：突出广州好吃，保留闺蜜互动', '脚本方案\n镜头从门头推进'):
            with self.subTest(text=text):
                result = normalize_script(text)
                self.assertFalse(result['strictReady'])
                self.assertEqual(result['normalizedText'], '')
                self.assertTrue(result['warnings'])

    def test_bounds_and_types(self):
        for text in (None, [], {}, 3, 'x' * 100001):
            with self.subTest(kind=type(text)), self.assertRaises(ValueError):
                normalize_script(text)

    def test_repeated_dialogue_not_deduplicated(self):
        self.assertEqual(self.lines('达人：好吃！\n达人：好吃！'), ['好吃！', '好吃！'])

    def test_source_line_available(self):
        result = normalize_script('标题：示例\n\n达人：这个真的香！')
        self.assertEqual(result['targets'][0]['sourceLine'], 3)

    def test_dialogue_column_keeps_unlabelled_price_and_short_replies(self):
        source = '场景\t文案\t画面内容\n山顶\t小林：我们先切然后开始卖<br>五块钱，扫那边<br>真香<br>扫码的镜头<br>小林：十分钟就发大财了\t游客买瓜'
        result = normalize_script(source)
        self.assertEqual(self.lines(source), ['我们先切然后开始卖', '五块钱，扫那边', '真香', '十分钟就发大财了'])
        self.assertTrue(all(t['scene'] == '山顶' for t in result['targets']))
        self.assertIn('扫码的镜头', [s['text'] for s in result['shotRequirements']])
        self.assertEqual(result['sourceText'], source)
        self.assertEqual(self.lines('小林：“开始卖了。”\n五块钱，扫那边'), ['开始卖了。', '五块钱，扫那边'])

    def test_dialogue_context_does_not_turn_dish_titles_or_actor_actions_into_speech(self):
        source = '场景\t文案\n餐厅\t达人：来吃饭！<br>和味传统烧鹅<br>麻婆豆腐鲜脑花<br>达人：真的好吃。<br>广角城市景观，达人靠窗拍照<br>达人闪身步来到示例餐厅门口。<br>五块钱，扫那边'
        result = normalize_script(source)
        self.assertEqual(self.lines(source), ['来吃饭！', '真的好吃。', '五块钱，扫那边'])
        self.assertEqual([s['text'] for s in result['shotRequirements']], ['广角城市景观，达人靠窗拍照', '达人闪身步来到示例餐厅门口。'])

    def test_role_action_cues_are_not_different_people(self):
        labels = ['小林疑惑', '助理支支吾吾', '小林抢助力外骨骼', '助理抢不过',
                  '小林跑步并跳几步', '小林拿出手机', '助理气喘吁吁', '小林得意',
                  '助理指着产品', '助理喘气', '小林快速爬坡', '小林挥手',
                  '小林俯身研究', '助理嘲笑', '小林气喘吁吁', '小林抬头',
                  '下一秒切小林坐在地上', '小林（喘气）']
        source = '场景\t文案\n爬山\t' + '<br>'.join(label + '：我来试试。' for label in labels)
        result = normalize_script(source)
        self.assertEqual(len(result['targets']), len(labels))
        self.assertEqual([t['speaker'] for t in result['targets']], ['助理' if label.startswith('助理') else '小林' for label in labels])
        self.assertTrue(all(t.get('speakerCue') for t in result['targets']))
        self.assertEqual(self.lines('达人觉得这个可以吗：拍一下吧'), [])

    def test_synthetic_parentheses_and_continuation_not_live_speech(self):
        source = '场景\t文案\n坡道\t小林：有AI？！<br>（ai配音）中国太厉害了！<br>机器人帮忙爬山。<br>助理：别跑太快！'
        result = normalize_script(source)
        self.assertEqual(self.lines(source), ['有AI？！', '别跑太快！'])
        self.assertEqual([x['kind'] for x in result['excludedContent']], ['synthetic_audio', 'synthetic_audio'])
        self.assertEqual(self.lines('达人：真的！（AI画外配音）太神奇了！'), ['真的！'])
        self.assertEqual(self.lines('达人：好吃。AI画外音：太香了！ 闺蜜：我也要。'), ['好吃。', '我也要。'])
        self.assertEqual(self.lines('角色意图：AI配音｜台词意图：机器人帮忙爬山'), [])

    def test_synthetic_role_column_and_plain_label_are_excluded(self):
        source = '| 场景 | 人物 | 台词 |\n| 坡道 | AI画外配音 | 中国太厉害了！ |\n| 坡道 | 小林 | 有AI？！ |'
        result = normalize_script(source)
        self.assertEqual(self.lines(source), ['有AI？！'])
        self.assertEqual(result['excludedContent'][0]['text'], '中国太厉害了！')
        self.assertEqual(self.lines('AI旁白\n中国太厉害了！\n达人：我也想试试！'), ['我也想试试！'])
        self.assertEqual(self.lines('达人：他说AI配音很厉害。'), ['他说AI配音很厉害。'])

    def test_shots_follow_scene_and_unnumbered_continuation_row(self):
        source = '序号\t场景\t文案\t画面内容\t拍摄方式\n1\t山脚\t小林：开始爬山！\t小林背瓜出发；助理指着机器\t中景＋近景正反打\n2\t山顶\t小林：到了！\t小林切瓜\t手持跟拍\n\t\t五块钱，扫那边<br>扫码的镜头\t游客接瓜\t\n3\t山下\t助理：赚了多少？\t两人笑着下山\t固定机位'
        result = normalize_script(source)
        self.assertEqual([t['scene'] for t in result['targets']], ['山脚', '山顶', '山顶', '山下'])
        expected_ids = {'山脚': [1], '山顶': [2, 3], '山下': [4]}
        for shot in result['shotRequirements']:
            self.assertEqual(shot['targetIds'], expected_ids[shot['scene']])
            self.assertGreaterEqual(shot['sourceLine'], 2)
            self.assertTrue(shot['sourceRefs'])
            self.assertNotIn('中景', shot['text'])
        self.assertEqual([s['id'] for s in result['shotRequirements']], ['shot-' + str(i + 1) for i in range(len(result['shotRequirements']))])

    def test_visual_actions_are_preserved_but_graphics_and_ai_are_not_shots(self):
        source = '场景\t文案\t画面内容\n坡道\t小林：我会跑！\t花字“15分钟后”，小林跑步、蹦跳；穿插营业额上涨的花字；AI画外配音点破“摘电池”；助理露出坏笑，为后文埋伏笔。'
        result = normalize_script(source)
        texts = [s['text'] for s in result['shotRequirements']]
        self.assertEqual(texts, ['小林跑步、蹦跳', '助理露出坏笑'])
        self.assertTrue(any(x['kind'] == 'synthetic_audio' and '摘电池' in x['text'] for x in result['excludedContent']))
        self.assertTrue(any(x['kind'] == 'postproduction' for x in result['excludedContent']))

    def test_camera_notes_extract_actual_content_not_pure_camera_technique(self):
        source = '场景\t文案\t拍摄方式\n休息点\t助理：把电池扣了\t摘电池给手部＋电池快拆特写；中景＋近景正反打；机位要低；长焦跟拍；手机时间特写，花字转场'
        result = normalize_script(source)
        self.assertEqual([s['text'] for s in result['shotRequirements']], ['摘电池给手部＋电池快拆特写', '手机时间特写'])
        self.assertTrue(all(s['sourceColumn'] == '拍摄方式' for s in result['shotRequirements']))

    def test_camera_parentheses_and_inner_thoughts_are_not_visual_requirements(self):
        source = '场景\t文案\t画面内容\t拍摄方式\n坡道\t小林：有AI？！\t助理露出坏笑、心生一计“看我怎么整你”；小林感叹中国太厉害了\t外骨骼电机运转特写（产品核心镜头，可搭AI音）；算账给账单特写＋利润花字'
        result = normalize_script(source)
        self.assertEqual([s['text'] for s in result['shotRequirements']], ['助理露出坏笑', '外骨骼电机运转特写', '算账给账单特写'])
        self.assertTrue(any(x['kind'] == 'nonvisual_description' for x in result['excludedContent']))
        self.assertTrue(any(x['kind'] == 'production_note' for x in result['excludedContent']))

    def test_distinct_shots_are_not_truncated_or_forcibly_combined(self):
        actions = ['小林放下背包', '助理摘下电池', '小林抬腿', '小林坐到地上', '助理递水', '两人下山']
        result = normalize_script('场景\t文案\t画面内容\n台阶\t小林：休息一下！\t' + '；'.join(actions))
        self.assertEqual([s['text'] for s in result['shotRequirements']], actions)
        self.assertTrue(all(s['targetIds'] == [1] for s in result['shotRequirements']))

    def test_plain_scene_labels_and_visual_only_table(self):
        result = normalize_script('场景：山顶\n画面：小林切瓜\n达人：我也要一片！')
        self.assertEqual(result['targets'][0]['scene'], '山顶')
        self.assertEqual(result['shotRequirements'][0]['targetIds'], [1])
        result = normalize_script('场景\t画面内容\t拍摄方式\n山顶\t小林切瓜\t固定机位')
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['shotRequirements'][0]['text'], '小林切瓜')
        self.assertEqual(result['shotRequirements'][0]['targetIds'], [])

    def test_docx_headers_and_scan_preparation(self):
        def cell(text):
            return '<w:tc><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:tc>'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '脚本.docx'
            docx(path, '<w:tbl><w:tr>' + ''.join(map(cell, ['角色', '台词', '画面'])) + '</w:tr><w:tr>' + ''.join(map(cell, ['小林', '我来试试！', '你要靠近镜头'])) + '</w:tr></w:tbl>')
            result = intake.scan([str(path)], Mock())
            script = result['scripts'][0]
            self.assertIn('\t', script['text'])
            self.assertEqual(script['preparation']['sourceText'], script['text'])
            self.assertEqual(script['preparation']['targets'][0]['speaker'], '小林')
            self.assertEqual(script['preparation']['targets'][0]['text'], '我来试试！')

    def test_docx_two_column_headers_survive_mixed_known_and_new_names(self):
        def cell(text):
            return '<w:tc><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:tc>'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '脚本.docx'
            rows = [['角色', '台词'], ['达人', '我来试试！'], ['小美', '真香！']]
            docx(path, '<w:tbl>' + ''.join('<w:tr>' + ''.join(map(cell,row)) + '</w:tr>' for row in rows) + '</w:tbl>')
            result = normalize_script(intake.script_text(path))
            self.assertEqual([t['text'] for t in result['targets']], ['我来试试！', '真香！'])
            self.assertEqual([t['speaker'] for t in result['targets']], ['达人', '小美'])


if __name__ == '__main__':
    unittest.main()
