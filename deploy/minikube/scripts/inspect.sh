#!/usr/bin/env bash
# inspect.sh — one-command, READ-ONLY audit of the analyzer-ng DB state.
# Prints stage-relevant counts + detail for every table the exerciser touches.
# Safe to run anytime; issues only SELECTs. Pipe to a file to snapshot state.
#
#   deploy/minikube/scripts/inspect.sh            # to stdout
#   deploy/minikube/scripts/inspect.sh | tee snapshot.txt
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/_lib.sh"

c_hdr "analyzer-ng DB AUDIT  ($(date '+%Y-%m-%d %H:%M:%S'))"
c_sub "health"
health | jp

c_sub "row counts (all touched tables)"
psql_c "select 'log_template' t,count(*) n from log_template
 union all select 'failure_signature',count(*) from failure_signature
 union all select 'failure_signature (has_vector)',count(*) from failure_signature where emb is not null
 union all select 'launch_group',count(*) from launch_group
 union all select 'failure_mode',count(*) from failure_mode
 union all select 'mode_membership',count(*) from mode_membership
 union all select 'suggestion',count(*) from suggestion
 union all select 'label_event',count(*) from label_event
 union all select 'test_item',count(*) from test_item
 union all select 'test_history_stats',count(*) from test_history_stats
 union all select 'model_artifact',count(*) from model_artifact
 union all select 'metrics_daily',count(*) from metrics_daily
 union all select 'llm_event',count(*) from llm_event
 order by t;"

c_sub "log_template (patterns / match_count)"
psql_c "select template_id,token_count,match_count,left(pattern,58) pattern,left(example,40) example from log_template order by match_count desc nulls last limit 15;"

c_sub "failure_signature (item_id, exception_fp, error_hash, emb_model_ver, has_vector)"
psql_c "select item_id,exception_fp,error_hash,emb_model_ver,(emb is not null) has_vector,left(coalesce(exc_text,''),34) exc from failure_signature order by item_id desc limit 20;"
psql_c "select emb_model_ver,count(*) sigs,count(*) filter (where emb is not null) with_vector from failure_signature group by emb_model_ver order by emb_model_ver;"

c_sub "launch_group (fingerprint, member_count, dominant, si_prior)  [burst = member_count>=5, si_prior>0]"
psql_c "select group_id,launch_id,fingerprint,member_count,dominant,si_prior from launch_group order by si_prior desc, member_count desc limit 15;"

c_sub "failure_mode (label, seed_key, status, purity, support, has_centroid, ewma_hit_rate)"
psql_c "select mode_id,seed_key,label,status,purity,support,(centroid is not null) has_centroid,ewma_hit_rate,emb_model_ver from failure_mode order by mode_id;"

c_sub "mode_membership (mode<->item links; feeds purity/centroid)"
psql_c "select mode_id,item_id,match_score,matched_by,created_at from mode_membership order by created_at desc limit 15;"

c_sub "suggestion (label, conf, matched_mode/item, model_ver, n_feature_keys, outcome)"
psql_c "select suggestion_id,item_id,predicted_label lbl,confidence conf,matched_mode_id mmode,matched_item_id mitem,
 (select count(*) from jsonb_object_keys(s.features)) nfeat,
 round((features->>'top1_cosine')::numeric,3) top1cos, model_ver, outcome
 from suggestion s order by suggestion_id desc limit 20;"
psql_c "select split_part(model_ver,';',1) method,count(*) n from suggestion group by 1 order by n desc;"

c_sub "label_event (last 10)"
psql_c "select event_id,item_id,old_label,new_label,source,ts from label_event order by event_id desc limit 10;"
psql_c "select substr(new_label,1,2) base_label,count(*) n from label_event group by 1 order by n desc;"

c_sub "test_history_stats"
psql_c "select test_case_hash,window_runs,window_failures,window_flips,last_status,round(flakiness_score::numeric,3) flakiness from test_history_stats order by updated_at desc limit 10;"

c_sub "model_artifact (kind, version, is_active, macro_f1)"
psql_c "select model_id,kind,version,feature_schema_ver fsv,n_events,is_active,round((metrics->>'macro_f1')::numeric,4) macro_f1,trained_at from model_artifact order by model_id;"

c_sub "metrics_daily"
psql_c "select day,suggestions,accepted,corrected,ignored,abstained,auto_labeled,auto_corrected,model_ver,emb_model_ver from metrics_daily order by day desc limit 7;"

c_sub "llm_event count"
psql_c "select count(*) llm_events, count(*) filter (where cache_hit) cache_hits from llm_event;"

c_hdr "END AUDIT"
