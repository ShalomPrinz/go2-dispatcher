from contract.vision import COCO_CLASSES


def test_coco_classes_are_the_80_unique_yolo_names():
    assert len(COCO_CLASSES) == len(set(COCO_CLASSES)) == 80
    assert COCO_CLASSES[0] == "person" and COCO_CLASSES[-1] == "toothbrush"
